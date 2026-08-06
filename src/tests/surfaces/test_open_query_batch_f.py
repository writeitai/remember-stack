"""Batch F: open-query facade, surfaces, freeze, telemetry, offline gate."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
import re
from uuid import UUID
from uuid import uuid4

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
import psycopg
import pytest
from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.engine import make_url

from rememberstack.core import CONSUMPTION_SKILL_VERSION
from rememberstack.core import render_consumption_skill
from rememberstack.core.open_query_prose import CORRECT_FACTS_CURRENT_SQL
from rememberstack.core.open_query_prose import FULL_AUDIT_TRAIL_SQL
from rememberstack.core.open_query_prose import LATEST_CONTRADICTING_TESTIMONY_SQL
from rememberstack.core.open_query_prose import PREDICATE_VOCABULARY_SQL
from rememberstack.core.open_query_prose import SNAPSHOT_ID_TO_LIVE_SQL
from rememberstack.core.open_query_prose import TWO_LAYER_HEADLINE_FULL
from rememberstack.core.open_query_prose import TWO_LAYER_HEADLINE_NOTE
from rememberstack.core.open_query_prose import WRONG_CLAIM_WINDOW_CURRENT_TRUTH_SQL
from rememberstack.eval.open_query_noninferiority import estimate_paid_run
from rememberstack.eval.open_query_noninferiority import evaluate_noninferiority
from rememberstack.model import ConsumptionDeployment
from rememberstack.model import ConsumptionRecipe
from rememberstack.model import ConsumptionSkillContext
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import Grain
from rememberstack.model import RecipeAnswerIntent
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine.query_space.canonical import surface_manifest_hash
from rememberstack.spine.query_space.manifest import build_hash_members
from rememberstack.spine.settings import load_database_settings
from rememberstack.surfaces.http_api import build_api
from rememberstack.surfaces.mcp import RecipeMcpServer
from rememberstack.surfaces.query_sandbox.audit import MigrationUsageCounters
from rememberstack.surfaces.query_sandbox.discovery import TWO_LAYER_HEADLINE
from rememberstack.surfaces.query_sandbox.errors import QueryErrorCode
from rememberstack.surfaces.query_sandbox.errors import SandboxRejection
from rememberstack.surfaces.query_sandbox.executor import QuerySandboxExecutor
from rememberstack.surfaces.query_sandbox.mcp_tools import open_query_tool_descriptors
from rememberstack.surfaces.query_sandbox.mcp_tools import OPEN_QUERY_TOOL_NAMES
from rememberstack.surfaces.query_sandbox.open_query import OpenQueryFacade
from rememberstack.surfaces.query_sandbox.saved_queries import PLATFORM_SEED_ACTOR
from rememberstack.surfaces.query_sandbox.saved_queries import SavedQueryDescription
from rememberstack.surfaces.query_sandbox.saved_queries import SavedQueryRegistry
from rememberstack.surfaces.query_sandbox.saved_queries import seed_shipped_examples

_DEPLOYMENT = UUID("f0000000-0000-0000-0000-00000000000f")
_HASH = surface_manifest_hash(build_hash_members())
_QUERY_ROLE_PASSWORD = "batch-f-proofs"
_ROOT = Path(__file__).resolve().parents[3]
# Raw connections opened by `_registry` for this module. Closed on fixture teardown
# so they cannot hold sessions that block the next module's schema downgrade.
_REGISTRY_CONNECTIONS: list[psycopg.Connection] = []


def _psycopg_url(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://")


@pytest.fixture(scope="module")
def migrated() -> Iterator[str]:
    """Fresh module-scoped DB for Batch F focused proofs."""
    database_url = load_database_settings().sqlalchemy_url()
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
        connection.execute(text("DROP SCHEMA IF EXISTS memory_v1 CASCADE"))
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config=config, revision="head")
    DeploymentBootstrapper(engine=engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=_DEPLOYMENT,
            slug="batch-f",
            name="Batch F",
            default_language="en",
            raw_bucket="s3://raw",
            artifacts_bucket="s3://artifacts",
            corpusfs_bucket="s3://corpusfs",
        )
    )
    with psycopg.connect(_psycopg_url(database_url)) as connection:
        connection.autocommit = True
        database = make_url(database_url).database or ""
        role = f"rememberstack_query_{database}"
        # Deploy-time fixture provisioning only (not request path).
        alter_sql: object = f"ALTER ROLE {role} PASSWORD '{_QUERY_ROLE_PASSWORD}'"
        grant_sql: object = f"GRANT {role} TO CURRENT_USER"
        connection.execute(alter_sql)  # type: ignore[arg-type]
        connection.execute(grant_sql)  # type: ignore[arg-type]
        seed_shipped_examples(
            connection=connection, deployment_id=_DEPLOYMENT, manifest_hash=_HASH
        )
        connection.commit()
    try:
        yield database_url
    finally:
        while _REGISTRY_CONNECTIONS:
            connection = _REGISTRY_CONNECTIONS.pop()
            connection.close()
        engine.dispose()


def _as_query_role(database_url: str) -> psycopg.Connection:
    base = _psycopg_url(database_url)
    prefix, _, tail = base.partition("://")
    _, _, hostpart = tail.partition("@")
    database = make_url(database_url).database or ""
    role = f"rememberstack_query_{database}"
    return psycopg.connect(f"{prefix}://{role}:{_QUERY_ROLE_PASSWORD}@{hostpart}")


def _sql_executor(database_url: str) -> QuerySandboxExecutor:
    return QuerySandboxExecutor(
        deployment_id=_DEPLOYMENT, connect=lambda: _as_query_role(database_url)
    )


def _registry(database_url: str) -> SavedQueryRegistry:
    connection = psycopg.connect(_psycopg_url(database_url))
    _REGISTRY_CONNECTIONS.append(connection)
    return SavedQueryRegistry(
        connection=connection,
        deployment_id=_DEPLOYMENT,
        manifest_hash=_HASH,
        actor=PLATFORM_SEED_ACTOR,
        can_activate=lambda _actor: True,
    )


def _facade(
    database_url: str, *, usage: MigrationUsageCounters | None = None
) -> OpenQueryFacade:
    return OpenQueryFacade(
        deployment_id=_DEPLOYMENT,
        sql=_sql_executor(database_url),
        saved_queries=_registry(database_url),
        usage=usage or MigrationUsageCounters(),
    )


# --- facade -----------------------------------------------------------------


def test_facade_query_sql_and_discovery(migrated: str) -> None:
    from rememberstack.core.open_query_prose import HONESTY_WARNINGS
    from rememberstack.core.open_query_prose import RETRIEVAL_CHOICES
    from rememberstack.surfaces.query_sandbox.discovery import (
        query_space_description_payload,
    )

    facade = _facade(migrated)
    result = facade.query_sql(sql="SELECT count(*)::int AS n FROM documents_live")
    assert result.termination_reason == "completed", result.error_message
    assert result.grade == "exploratory_tabular"
    assert result.saved_query is None
    space = facade.describe_query_space(include_examples=True)
    assert space.headline == TWO_LAYER_HEADLINE_FULL
    assert TWO_LAYER_HEADLINE_NOTE in space.headline
    assert "`fact_claim_evidence`" in space.headline
    assert space.retrieval_choices == RETRIEVAL_CHOICES
    assert space.honesty_warnings == HONESTY_WARNINGS
    assert len(space.worked_examples) >= 8
    keys = {str(item["key"]) for item in space.worked_examples}
    assert "wrong_claim_window_current_truth" in keys
    assert "native_cypher_traversal_aggregation" in keys
    assert "semantic_to_relational" in keys
    assert space.core_operation_descriptors
    assert space.function_signatures
    assert space.sql_grammar
    assert space.cypher_dialect
    assert space.p2_projection
    assert len(space.examples) == 17
    assert len(space.worked_examples) == 8
    cypher_example = next(
        item
        for item in space.worked_examples
        if item["key"] == "native_cypher_traversal_aggregation"
    )
    body = str(cypher_example["body"])
    assert "MATCH" in body and "RELATES" in body and "count(" in body.lower()
    payload = query_space_description_payload(space)
    for field in (
        "core_operation_descriptors",
        "function_signatures",
        "sql_grammar",
        "cypher_dialect",
        "p2_projection",
        "limits",
        "retrieval_choices",
        "honesty_warnings",
        "worked_examples",
    ):
        assert field in payload
    hits = facade.search_query_space(query="current facts", k=3)
    assert hits
    assert any(hit.name == "facts_current" for hit in hits)
    example_hits = facade.search_query_space(query="claims_verbatim", k=5)
    assert any(hit.kind == "example" for hit in example_hits)


def test_facade_rejects_mismatched_sql_deployment(migrated: str) -> None:
    sql = QuerySandboxExecutor(
        deployment_id=uuid4(), connect=lambda: _as_query_role(migrated)
    )
    with pytest.raises(ValueError, match="different deployment"):
        OpenQueryFacade(deployment_id=_DEPLOYMENT, sql=sql)


def test_run_saved_query_stamps_and_uses_sql_executor(migrated: str) -> None:
    facade = _facade(migrated)
    listed = facade.list_saved_queries(namespace="examples")
    assert len(listed) == 17
    sample = next(row for row in listed if row.name == "relation_current")
    outcome = facade.run_saved_query(
        namespace="examples",
        name="relation_current",
        version=sample.version,
        parameters=(uuid4(),),
    )
    assert outcome.termination_reason == "completed", outcome.error_message
    assert outcome.saved_query is not None
    # Design §4.4 exact stamp keys and names (string-valued).
    assert set(outcome.saved_query) == {
        "query_id",
        "namespace",
        "name",
        "version",
        "query_hash",
    }
    assert outcome.saved_query["query_id"] == str(sample.query_id)
    assert outcome.saved_query["namespace"] == "examples"
    assert outcome.saved_query["name"] == "relation_current"
    assert outcome.saved_query["version"] == str(sample.version)
    assert outcome.saved_query["query_hash"] == sample.query_hash


def test_run_saved_query_exact_version_must_be_active(migrated: str) -> None:
    connection = psycopg.connect(_psycopg_url(migrated))
    registry = SavedQueryRegistry(
        connection=connection,
        deployment_id=_DEPLOYMENT,
        manifest_hash=_HASH,
        actor="agent-batch-f",
        can_activate=lambda _actor: True,
    )
    draft = registry.draft(
        namespace="customer", name="noop", sql="SELECT 1 AS n WHERE $1::boolean"
    )
    connection.commit()
    # draft is not executable even by exact version
    facade = OpenQueryFacade(
        deployment_id=_DEPLOYMENT, sql=_sql_executor(migrated), saved_queries=registry
    )
    with pytest.raises(SandboxRejection) as caught:
        facade.run_saved_query(
            namespace="customer", name="noop", version=draft.version, parameters=(True,)
        )
    assert caught.value.code == QueryErrorCode.SAVED_QUERY_DISABLED
    connection.close()


def test_run_saved_query_not_found(migrated: str) -> None:
    facade = _facade(migrated)
    with pytest.raises(SandboxRejection) as caught:
        facade.run_saved_query(namespace="missing", name="nope", parameters=())
    assert caught.value.code == QueryErrorCode.SAVED_QUERY_NOT_FOUND


def test_run_saved_query_applies_stored_default_limits(migrated: str) -> None:
    """Stored max_rows, statement_timeout_ms, and max_bytes all affect QueryResult."""
    from rememberstack.surfaces.query_sandbox.saved_queries import SavedQueryVersion

    class _LimitsRegistry:
        """Minimal resolve surface that returns fixed stored default_limits."""

        @property
        def deployment_id(self) -> UUID:
            return _DEPLOYMENT

        def list_saved_queries(
            self, *, namespace: str | None = None, status: str | None = None
        ) -> tuple:
            return ()

        def describe_saved_query(
            self, *, namespace: str, name: str, version: int | None = None
        ) -> SavedQueryDescription:
            raise AssertionError("not used")

        def resolve(
            self, *, namespace: str, name: str, version: int | None = None
        ) -> SavedQueryVersion:
            return SavedQueryVersion(
                query_id=uuid4(),
                version=1,
                namespace="customer",
                name="limits_probe",
                sql="SELECT 1 AS n",
                query_hash="fixed-for-limits-probe",
                parameter_schema={},
                status="active",
                validated_surface_manifest_hash=_HASH,
                assurance="customer_authored",
                default_limits={
                    "max_rows": 7,
                    "statement_timeout_ms": 1234,
                    "max_bytes": 4096,
                },
            )

    facade = OpenQueryFacade(
        deployment_id=_DEPLOYMENT,
        sql=_sql_executor(migrated),
        saved_queries=_LimitsRegistry(),
    )
    outcome = facade.run_saved_query(
        namespace="customer", name="limits_probe", parameters=()
    )
    assert outcome.termination_reason == "completed", outcome.error_message
    assert outcome.limits.row_cap == 7
    assert outcome.limits.statement_timeout_ms == 1234
    assert outcome.limits.byte_cap == 4096
    # Caller-provided max_rows wins over the stored default.
    override = facade.run_saved_query(
        namespace="customer", name="limits_probe", parameters=(), max_rows=3
    )
    assert override.limits.row_cap == 3
    assert override.limits.statement_timeout_ms == 1234
    assert override.limits.byte_cap == 4096

    # Stored values above interactive hard caps but within Batch E absolute
    # analytical hard caps (10_000 rows, 60_000 ms, 67_108_864 bytes) clamp
    # down to interactive when the run uses the interactive tier.
    class _ClampedRegistry(_LimitsRegistry):
        def resolve(
            self, *, namespace: str, name: str, version: int | None = None
        ) -> SavedQueryVersion:
            base = super().resolve(namespace=namespace, name=name, version=version)
            return SavedQueryVersion(
                query_id=base.query_id,
                version=base.version,
                namespace=base.namespace,
                name=base.name,
                sql=base.sql,
                query_hash=base.query_hash,
                parameter_schema=base.parameter_schema,
                status=base.status,
                validated_surface_manifest_hash=base.validated_surface_manifest_hash,
                assurance=base.assurance,
                default_limits={
                    "max_rows": 10_000,
                    "statement_timeout_ms": 60_000,
                    "max_bytes": 67_108_864,
                },
            )

    clamped_facade = OpenQueryFacade(
        deployment_id=_DEPLOYMENT,
        sql=_sql_executor(migrated),
        saved_queries=_ClampedRegistry(),
    )
    clamped = clamped_facade.run_saved_query(
        namespace="customer", name="limits_probe", parameters=()
    )
    assert clamped.limits.row_cap == 1_000  # interactive hard
    assert clamped.limits.statement_timeout_ms == 15_000
    assert clamped.limits.byte_cap == 8_388_608


def test_public_deployment_id_properties(migrated: str) -> None:
    """Facade reads public deployment_id properties, not private attributes."""
    sql = _sql_executor(migrated)
    registry = _registry(migrated)
    assert sql.deployment_id == _DEPLOYMENT
    assert registry.deployment_id == _DEPLOYMENT
    facade = OpenQueryFacade(deployment_id=_DEPLOYMENT, sql=sql, saved_queries=registry)
    assert facade.deployment_id == _DEPLOYMENT


# --- HTTP / MCP -------------------------------------------------------------


class _OpenBoundary:
    def ensure_ready(self, *, deployment_id: UUID) -> tuple[UUID, ...]:
        return ()

    def assert_available(self, *, deployment_id: UUID) -> None:
        return None


class _NullEngine:
    """QueryEngine stand-in; open routes do not use it."""

    def resolve(self, **_: object) -> None:
        raise AssertionError("not used")


def test_http_open_routes_and_legacy_recipes_untouched(migrated: str) -> None:
    from rememberstack.adapters.testing import FakeModelProvider
    from rememberstack.spine import RecipeRegistry
    from rememberstack.spine import seed_canonical_recipes
    from rememberstack.surfaces import QueryEngine
    from rememberstack.surfaces import RecipeExecutor
    from rememberstack.surfaces import RecipeSurface

    engine = create_engine(migrated)
    seed_canonical_recipes(
        registry=RecipeRegistry(engine=engine), deployment_id=_DEPLOYMENT
    )
    usage = MigrationUsageCounters()
    query_engine = QueryEngine(
        engine=engine,
        search_index=_NullSearch(),
        model_provider=FakeModelProvider(),
        embedding_model="test/embed",
    )
    facade = _facade(migrated, usage=usage)
    surface = RecipeSurface(
        registry=RecipeRegistry(engine=engine),
        executor=RecipeExecutor(query_engine=query_engine),
        deployment_id=_DEPLOYMENT,
        usage=usage,
    )
    app = build_api(
        engine=query_engine,
        deployment_id=_DEPLOYMENT,
        admission=_OpenBoundary(),
        readiness=_OpenBoundary(),
        surface=surface,
        open_query=facade,
    )
    client = TestClient(app)
    recipes = client.get("/recipes")
    assert recipes.status_code == 200
    names = {row["name"] for row in recipes.json()}
    assert "resolve_entity" in names
    # open routes — full first-call discovery, not a shortened subset
    space = client.get("/query/space")
    assert space.status_code == 200
    space_body = space.json()
    assert space_body["headline"] == TWO_LAYER_HEADLINE_FULL
    assert TWO_LAYER_HEADLINE_NOTE in space_body["headline"]
    assert "`fact_claim_evidence`" in space_body["headline"]
    assert "core_operation_descriptors" in space_body
    assert "function_signatures" in space_body
    assert "cypher_dialect" in space_body
    assert "p2_projection" in space_body
    assert "worked_examples" in space_body
    assert "retrieval_choices" in space_body
    assert "honesty_warnings" in space_body
    search = client.get(
        "/query/space/search", params={"query": "facts_current", "k": 5}
    )
    assert search.status_code == 200
    assert any(row.get("name") == "facts_current" for row in search.json())
    sql = client.post("/query/sql", json={"sql": "SELECT 1 AS n", "parameters": []})
    assert sql.status_code == 200
    body = sql.json()
    assert body["contract"] == "QueryResult/v1"
    assert body["termination_reason"] == "completed"
    # Grammar rejections return QueryResult with a public error_code (not a crash)
    bad = client.post(
        "/query/sql", json={"sql": "DELETE FROM claims", "parameters": []}
    )
    assert bad.status_code == 200
    rejected = bad.json()
    assert rejected["termination_reason"] == "rejected"
    assert rejected["error_code"] == "statement_not_allowed"
    assert rejected["error_message"] is not None
    assert "pg_" not in rejected["error_message"].lower()


class _NullSearch:
    def search_claims(self, **_: object) -> tuple:
        return ()

    def search_claims_lexical(self, **_: object) -> tuple:
        return ()

    def search_chunks(self, **_: object) -> tuple:
        return ()

    def search_chunks_lexical(self, **_: object) -> tuple:
        return ()

    def search_facts(self, **_: object) -> tuple:
        return ()

    def search_entities(self, **_: object) -> tuple:
        return ()

    def chunk_texts(self, **_: object) -> dict:
        return {}


def test_mcp_lists_nine_open_tools_not_examples(migrated: str) -> None:
    from rememberstack.adapters.testing import FakeModelProvider
    from rememberstack.spine import RecipeRegistry
    from rememberstack.spine import seed_canonical_recipes
    from rememberstack.surfaces import QueryEngine
    from rememberstack.surfaces import RecipeExecutor
    from rememberstack.surfaces import RecipeSurface

    engine = create_engine(migrated)
    seed_canonical_recipes(
        registry=RecipeRegistry(engine=engine), deployment_id=_DEPLOYMENT
    )
    query_engine = QueryEngine(
        engine=engine,
        search_index=_NullSearch(),
        model_provider=FakeModelProvider(),
        embedding_model="test/embed",
    )
    surface = RecipeSurface(
        registry=RecipeRegistry(engine=engine),
        executor=RecipeExecutor(query_engine=query_engine),
        deployment_id=_DEPLOYMENT,
    )
    server = RecipeMcpServer(surface=surface, open_query=_facade(migrated))
    tools = server.list_tools()["tools"]
    assert isinstance(tools, list)
    names = {tool["name"] for tool in tools}  # type: ignore[index]
    for name in OPEN_QUERY_TOOL_NAMES:
        assert name in names
    assert not any(str(name).startswith("examples.") for name in names)
    assert "resolve_entity" in names
    # descriptors are exactly the nine static infrastructure tools
    assert {d["name"] for d in open_query_tool_descriptors()} == set(
        OPEN_QUERY_TOOL_NAMES
    )
    called = server.call_tool(
        name="describe_query_space", arguments={"include_examples": True}
    )
    assert called["isError"] is False
    import json

    described = json.loads(called["content"][0]["text"])  # type: ignore[index]
    assert "core_operation_descriptors" in described
    assert "worked_examples" in described


def test_local_mcp_strict_argument_validation(migrated: str) -> None:
    """Local MCP rejects false-string booleans, wrong types, and unknown keys."""
    from rememberstack.adapters.testing import FakeModelProvider
    from rememberstack.spine import RecipeRegistry
    from rememberstack.spine import seed_canonical_recipes
    from rememberstack.surfaces import QueryEngine
    from rememberstack.surfaces import RecipeExecutor
    from rememberstack.surfaces import RecipeSurface

    engine = create_engine(migrated)
    seed_canonical_recipes(
        registry=RecipeRegistry(engine=engine), deployment_id=_DEPLOYMENT
    )
    surface = RecipeSurface(
        registry=RecipeRegistry(engine=engine),
        executor=RecipeExecutor(
            query_engine=QueryEngine(
                engine=engine,
                search_index=_NullSearch(),
                model_provider=FakeModelProvider(),
                embedding_model="test/embed",
            )
        ),
        deployment_id=_DEPLOYMENT,
    )
    server = RecipeMcpServer(surface=surface, open_query=_facade(migrated))

    false_string = server.call_tool(
        name="query_cypher",
        arguments={"cypher": "MATCH (n) RETURN n LIMIT 1", "confirm": "false"},
    )
    assert false_string["isError"] is True
    assert "boolean" in str(false_string["content"]).lower()

    wrong_sql_type = server.call_tool(
        name="query_sql", arguments={"sql": 123, "parameters": []}
    )
    assert wrong_sql_type["isError"] is True
    assert "string" in str(wrong_sql_type["content"]).lower()

    wrong_sql_params = server.call_tool(
        name="query_sql", arguments={"sql": "SELECT 1", "parameters": {"a": 1}}
    )
    assert wrong_sql_params["isError"] is True
    assert "array" in str(wrong_sql_params["content"]).lower()

    wrong_cypher_params = server.call_tool(
        name="query_cypher",
        arguments={"cypher": "MATCH (n) RETURN n", "parameters": ["x"]},
    )
    assert wrong_cypher_params["isError"] is True
    assert "object" in str(wrong_cypher_params["content"]).lower()

    unknown_key = server.call_tool(
        name="describe_query_space", arguments={"extra": True}
    )
    assert unknown_key["isError"] is True
    assert "unknown" in str(unknown_key["content"]).lower()

    bool_as_int = server.call_tool(
        name="search_query_space", arguments={"query": "facts", "k": True}
    )
    assert bool_as_int["isError"] is True

    # Schema ranges, not just types: max_rows >= 0, version >= 1, k in 1..25.
    neg_rows = server.call_tool(
        name="query_sql", arguments={"sql": "SELECT 1", "max_rows": -1}
    )
    assert neg_rows["isError"] is True
    assert "max_rows" in str(neg_rows["content"]).lower()

    bad_k = server.call_tool(
        name="search_query_space", arguments={"query": "facts", "k": 0}
    )
    assert bad_k["isError"] is True
    assert "k" in str(bad_k["content"]).lower()

    high_k = server.call_tool(
        name="search_query_space", arguments={"query": "facts", "k": 26}
    )
    assert high_k["isError"] is True

    bad_version = server.call_tool(
        name="describe_saved_query",
        arguments={"namespace": "examples", "name": "claims_verbatim", "version": 0},
    )
    assert bad_version["isError"] is True
    assert "version" in str(bad_version["content"]).lower()


def test_remote_mcp_strict_argument_validation() -> None:
    """Remote MCP-to-SDK dispatch rejects the same invalid argument shapes."""
    from rememberstack.surfaces.query_sandbox.mcp_tools import (
        validate_open_query_arguments,
    )
    from rememberstack.surfaces.remote_mcp import RemoteRecipeMcpServer

    class _StubClient:
        """Minimal client that only exercises open-query argument validation."""

        def call_open_query(self, *, name: str, arguments: dict[str, object]) -> object:
            return validate_open_query_arguments(name=name, arguments=arguments)

        def recipes(self) -> list:
            return []

        def run_recipe(self, **_: object) -> object:
            raise AssertionError("not used")

    server = RemoteRecipeMcpServer(client=_StubClient())  # type: ignore[arg-type]
    false_string = server.call_tool(
        name="describe_query_space", arguments={"include_examples": "false"}
    )
    assert false_string["isError"] is True
    wrong_type = server.call_tool(
        name="search_query_space", arguments={"query": ["not", "a", "string"]}
    )
    assert wrong_type["isError"] is True
    unknown = server.call_tool(
        name="list_saved_queries", arguments={"namespace": "x", "bonus": 1}
    )
    assert unknown["isError"] is True
    # Positive control: valid args pass validation without HTTP.
    ok = server.call_tool(
        name="search_query_space", arguments={"query": "facts", "k": 3}
    )
    assert ok["isError"] is False


# --- skill / prose ----------------------------------------------------------


def test_skill_opens_with_bound_headline_and_examples() -> None:
    skill = render_consumption_skill(
        context=ConsumptionSkillContext(
            deployment=ConsumptionDeployment(
                deployment_id=_DEPLOYMENT,
                slug="batch-f",
                name="Batch F",
                description=None,
                default_language="en",
                scopes=(),
                knowledge_page_count=0,
            ),
            recipes=(
                ConsumptionRecipe(
                    name="resolve_entity",
                    description="Resolve a name.",
                    output_grain=Grain.FACT,
                    answer_intent=RecipeAnswerIntent.CURRENT_FACTS,
                ),
            ),
            mounts=None,
        )
    )
    assert skill.version == CONSUMPTION_SKILL_VERSION == "2.0.0"
    assert skill.content.startswith("---\n")
    # first prose after the skill title block is the bound headline
    assert TWO_LAYER_HEADLINE in skill.content
    assert TWO_LAYER_HEADLINE_NOTE in skill.content
    assert TWO_LAYER_HEADLINE_FULL in skill.content
    assert WRONG_CLAIM_WINDOW_CURRENT_TRUTH_SQL in skill.content
    assert CORRECT_FACTS_CURRENT_SQL in skill.content
    assert PREDICATE_VOCABULARY_SQL in skill.content
    assert FULL_AUDIT_TRAIL_SQL in skill.content
    assert LATEST_CONTRADICTING_TESTIMONY_SQL in skill.content
    assert SNAPSHOT_ID_TO_LIVE_SQL in skill.content
    # Same worked-example set as discovery (Cypher + semantic-to-relational too).
    from rememberstack.core.open_query_prose import bound_worked_examples

    for example in bound_worked_examples():
        assert str(example["body"]) in skill.content
        assert str(example["title"]) in skill.content
    assert "Choose how to query" in skill.content
    assert "recipe-first" not in skill.content.lower()
    assert "Default motion: orient, verify, audit" not in skill.content


# --- telemetry privacy ------------------------------------------------------


def test_migration_usage_is_content_free() -> None:
    usage = MigrationUsageCounters()
    usage.record(surface="compatibility_adapter", operation="relation_current")
    usage.record(surface="open_query", operation="query_sql")
    usage.record(surface="core_operation", operation="resolve_entity")
    snap = usage.snapshot()
    assert snap["compatibility_adapter_calls"] == 1
    assert snap["open_query_calls"] == 1
    assert snap["core_operation_calls"] == 1
    encoded = str(snap)
    assert "SELECT" not in encoded
    assert "MATCH" not in encoded
    assert "parameters" not in encoded


def test_facade_records_open_query_usage(migrated: str) -> None:
    usage = MigrationUsageCounters()
    facade = _facade(migrated, usage=usage)
    facade.query_sql(sql="SELECT 1")
    facade.query_sql(sql="SELECT 2")
    assert usage.open_query_calls == 2
    assert usage.compatibility_adapter_calls == 0


def test_discovery_and_explain_do_not_count_as_retrieval(migrated: str) -> None:
    """§8 denominator is retrieval-bearing only: not explain/discovery/list."""
    usage = MigrationUsageCounters()
    facade = _facade(migrated, usage=usage)
    before = usage.snapshot()
    facade.describe_query_space()
    facade.search_query_space(query="facts", k=3)
    facade.list_saved_queries()
    facade.explain_sql(sql="SELECT 1")
    after = usage.snapshot()
    assert after["open_query_calls"] == before["open_query_calls"] == 0
    assert after["by_operation"] == before["by_operation"] == {}
    # Retrieval still increments.
    facade.query_sql(sql="SELECT 1")
    assert usage.open_query_calls == 1


def test_usage_counters_require_explicit_opt_in() -> None:
    """The disabled factory is a no-op; hosts opt into enabled counters."""
    from rememberstack.surfaces.query_sandbox.audit import MigrationUsageCounters

    disabled = MigrationUsageCounters.disabled()
    disabled.record(surface="compatibility_adapter", operation="relation_current")
    assert disabled.compatibility_adapter_calls == 0
    enabled = MigrationUsageCounters()
    enabled.record(surface="compatibility_adapter", operation="relation_current")
    assert enabled.compatibility_adapter_calls == 1


# --- offline noninferiority gate --------------------------------------------


def test_offline_noninferiority_gates_and_estimate() -> None:
    plan = estimate_paid_run(cases=100, arms=2, calls_per_case=3, unit_cost=0.01)
    assert plan["paid_run"] is False
    assert plan["operator_gated"] is True
    assert plan["total_model_calls"] == 600
    assert plan["estimated_total_cost"] == 6.0


def test_estimate_paid_run_rejects_bool_and_non_finite_inputs() -> None:
    """Estimator stays trustworthy: bools and non-finite unit costs fail closed."""
    with pytest.raises(ValueError, match="cases"):
        estimate_paid_run(cases=True)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="arms"):
        estimate_paid_run(cases=1, arms=True)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="arms"):
        estimate_paid_run(cases=1, arms=0)
    with pytest.raises(ValueError, match="calls_per_case"):
        estimate_paid_run(cases=1, calls_per_case=False)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="unit_cost"):
        estimate_paid_run(cases=1, unit_cost=True)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="unit_cost"):
        estimate_paid_run(cases=1, unit_cost=float("nan"))
    with pytest.raises(ValueError, match="unit_cost"):
        estimate_paid_run(cases=1, unit_cost=float("inf"))
    with pytest.raises(ValueError, match="unit_cost"):
        estimate_paid_run(cases=1, unit_cost=-0.01)

    metrics = {
        "legacy": {
            "success_rate": 80.0,
            "critical_categories": {"A": 90.0, "B": 70.0},
            "d41_violations": 0,
            "d48_violations": 0,
            "d54_violations": 0,
            "cross_deployment_violations": 0,
            "p95_latency_ms": 100.0,
            "metered_cost": 10.0,
            "invalid_sql_rate": 0.0,
            "invalid_cypher_rate": 0.0,
            "caps_and_drops_visible": True,
        },
        "open": {
            "success_rate": 81.0,
            # Already-collected lower 95% of the open-vs-legacy success delta.
            "success_delta_lower_95": -1.0,  # >= -2
            "critical_categories": {"A": 88.0, "B": 66.0},  # -2 and -4 >= -5
            "d41_violations": 0,
            "d48_violations": 0,
            "d54_violations": 0,
            "cross_deployment_violations": 0,
            "p95_latency_ms": 120.0,  # 1.2x <= 1.25
            "metered_cost": 12.0,
            "invalid_sql_rate": 0.04,
            "invalid_cypher_rate": 0.03,
            "caps_and_drops_visible": True,
        },
    }
    report = evaluate_noninferiority(metrics=metrics)
    assert report["passed"] is True
    assert report["paid_run"] is False

    failing = {
        "legacy": metrics["legacy"],
        "open": {
            **metrics["open"],
            "success_delta_lower_95": -3.0,  # < -2
            "d48_violations": 1,
            "invalid_sql_rate": 0.1,
            "caps_and_drops_visible": False,
        },
    }
    failed = evaluate_noninferiority(metrics=failing)
    assert failed["passed"] is False
    names = {gate["name"] for gate in failed["gates"] if not gate["passed"]}  # type: ignore[index]
    assert "overall_success_delta_lower_95" in names
    assert "d48_violations" in names
    assert "invalid_sql_rate" in names
    assert "caps_and_drops_visible" in names


def test_offline_gate_rejects_invalid_metric_shapes() -> None:
    """Input validation: ranges, bool type, non-empty categories, required delta."""
    base_legacy = {
        "success_rate": 80.0,
        "critical_categories": {"A": 90.0},
        "d41_violations": 0,
        "d48_violations": 0,
        "d54_violations": 0,
        "cross_deployment_violations": 0,
        "p95_latency_ms": 100.0,
        "metered_cost": 10.0,
        "invalid_sql_rate": 0.0,
        "invalid_cypher_rate": 0.0,
        "caps_and_drops_visible": True,
    }
    base_open = {
        **base_legacy,
        "success_delta_lower_95": -1.0,
        "critical_categories": {"A": 88.0},
    }
    with pytest.raises(ValueError, match="success_delta_lower_95"):
        evaluate_noninferiority(
            metrics={"legacy": base_legacy, "open": {**base_legacy}}
        )
    with pytest.raises(ValueError, match="0..100"):
        evaluate_noninferiority(
            metrics={
                "legacy": base_legacy,
                "open": {**base_open, "success_rate": 120.0},
            }
        )
    with pytest.raises(ValueError, match="0..1"):
        evaluate_noninferiority(
            metrics={
                "legacy": base_legacy,
                "open": {**base_open, "invalid_sql_rate": 2.0},
            }
        )
    with pytest.raises(ValueError, match="boolean"):
        evaluate_noninferiority(
            metrics={
                "legacy": base_legacy,
                "open": {**base_open, "caps_and_drops_visible": 1},
            }
        )
    with pytest.raises(ValueError, match="non-empty"):
        evaluate_noninferiority(
            metrics={
                "legacy": {**base_legacy, "critical_categories": {}},
                "open": base_open,
            }
        )
    # Missing open category fails the critical-categories gate (not ValueError).
    report = evaluate_noninferiority(
        metrics={
            "legacy": {**base_legacy, "critical_categories": {"A": 90.0, "B": 70.0}},
            "open": base_open,
        }
    )
    assert report["passed"] is False
    names = {g["name"] for g in report["gates"] if not g["passed"]}  # type: ignore[index]
    assert "critical_categories" in names


def test_bound_sql_examples_match_design_strings() -> None:
    """Exact §6 bodies stay byte-stable as the shared product authority."""
    assert "FROM claims_live" in WRONG_CLAIM_WINDOW_CURRENT_TRUTH_SQL
    assert "FROM facts_current AS f" in CORRECT_FACTS_CURRENT_SQL
    assert "GROUP BY 1" in PREDICATE_VOCABULARY_SQL
    assert "fact_claim_evidence_live" in FULL_AUDIT_TRAIL_SQL
    assert "stance = 'contradicts'" in LATEST_CONTRADICTING_TESTIMONY_SQL
    assert "entities_current" in SNAPSHOT_ID_TO_LIVE_SQL


def test_compatibility_recipe_descriptors_remain_frozen(migrated: str) -> None:
    """Existing recipe names, versions, and input schemas stay byte-stable."""
    from rememberstack.adapters.testing import FakeModelProvider
    from rememberstack.spine import RecipeRegistry
    from rememberstack.spine import seed_canonical_recipes
    from rememberstack.spine.recipes import CANONICAL_RECIPES
    from rememberstack.surfaces import QueryEngine
    from rememberstack.surfaces import RecipeExecutor
    from rememberstack.surfaces import RecipeSurface

    engine = create_engine(migrated)
    seed_canonical_recipes(
        registry=RecipeRegistry(engine=engine), deployment_id=_DEPLOYMENT
    )
    surface = RecipeSurface(
        registry=RecipeRegistry(engine=engine),
        executor=RecipeExecutor(
            query_engine=QueryEngine(
                engine=engine,
                search_index=_NullSearch(),
                model_provider=FakeModelProvider(),
                embedding_model="test/embed",
            )
        ),
        deployment_id=_DEPLOYMENT,
    )
    descriptors = {item.name: item for item in surface.descriptors()}
    # Seventeen demoted + three core ops are the stock canonical set (no graph).
    expected_names = {recipe.name for recipe in CANONICAL_RECIPES}
    assert set(descriptors) == expected_names
    for recipe in CANONICAL_RECIPES:
        descriptor = descriptors[recipe.name]
        assert descriptor.version == recipe.version
        assert descriptor.input_schema == _expected_input_schema(recipe)
        assert descriptor.output_grain == recipe.output_grain.value
        assert descriptor.answer_intent == recipe.answer_intent.value
    # Pin the three assured versions explicitly
    assert descriptors["resolve_entity"].version == 1
    assert descriptors["question_context"].version == 4
    assert descriptors["current_context"].version == 1


def _expected_input_schema(recipe: object) -> dict[str, object]:
    """Rebuild the public input schema the same way RecipeSurface does."""
    from rememberstack.model import Recipe
    from rememberstack.surfaces.recipe_surface import recipe_descriptors

    assert isinstance(recipe, Recipe)
    return recipe_descriptors(recipes=(recipe,))[0].input_schema


# --- MemoryClient HTTP + CLI parse/dispatch ---------------------------------


def _open_api(migrated: str):
    """Compose the HTTP app with open-query for real client method tests."""
    from rememberstack.adapters.testing import FakeModelProvider
    from rememberstack.spine import RecipeRegistry
    from rememberstack.spine import seed_canonical_recipes
    from rememberstack.surfaces import QueryEngine
    from rememberstack.surfaces import RecipeExecutor
    from rememberstack.surfaces import RecipeSurface

    engine = create_engine(migrated)
    seed_canonical_recipes(
        registry=RecipeRegistry(engine=engine), deployment_id=_DEPLOYMENT
    )
    query_engine = QueryEngine(
        engine=engine,
        search_index=_NullSearch(),
        model_provider=FakeModelProvider(),
        embedding_model="test/embed",
    )
    facade = _facade(migrated)
    surface = RecipeSurface(
        registry=RecipeRegistry(engine=engine),
        executor=RecipeExecutor(query_engine=query_engine),
        deployment_id=_DEPLOYMENT,
    )
    return build_api(
        engine=query_engine,
        deployment_id=_DEPLOYMENT,
        admission=_OpenBoundary(),
        readiness=_OpenBoundary(),
        surface=surface,
        open_query=facade,
    )


def test_memory_client_open_query_http_methods(migrated: str) -> None:
    """Exercise real MemoryClient HTTP methods, not stub-only validation."""
    from rememberstack.client import MemoryClient

    app = _open_api(migrated)
    client = MemoryClient(client=TestClient(app))

    sql = client.query_sql(sql="SELECT 1 AS n", parameters=[])
    assert sql["contract"] == "QueryResult/v1"
    assert sql["termination_reason"] == "completed"

    space = client.describe_query_space(include_examples=True)
    headline = space["headline"]
    assert isinstance(headline, str)
    assert headline == TWO_LAYER_HEADLINE_FULL
    assert TWO_LAYER_HEADLINE_NOTE in headline
    assert "sql_grammar" in space
    worked = space["worked_examples"]
    assert isinstance(worked, list)
    assert len(worked) == 8

    hits = client.search_query_space(query="facts_current", k=5)
    assert isinstance(hits, list)
    assert any(row.get("name") == "facts_current" for row in hits)

    saved = client.run_saved_query(
        namespace="examples", name="relation_current", parameters=[str(uuid4())]
    )
    assert saved["contract"] == "QueryResult/v1"
    stamp = saved.get("saved_query")
    assert isinstance(stamp, dict)
    assert set(stamp) == {"query_id", "namespace", "name", "version", "query_hash"}
    assert stamp["namespace"] == "examples"
    assert stamp["name"] == "relation_current"
    assert isinstance(stamp["query_id"], str) and stamp["query_id"]
    assert isinstance(stamp["version"], str) and stamp["version"]
    assert isinstance(stamp["query_hash"], str) and stamp["query_hash"]


def test_cli_open_query_parse_and_dispatch(migrated: str, monkeypatch, capsys) -> None:
    """CLI parser accepts positional SQL/saved-query forms and dispatches them."""
    import json

    from rememberstack.client import MemoryClient
    from rememberstack.surfaces.cli import main

    app = _open_api(migrated)
    real_client = MemoryClient(client=TestClient(app))

    class _Factory:
        """Stand-in for MemoryClient.from_settings() used by the CLI."""

        @classmethod
        def from_settings(cls, *args: object, **kwargs: object) -> MemoryClient:
            return real_client

        def __call__(self, *args: object, **kwargs: object) -> MemoryClient:
            return real_client

    monkeypatch.setattr("rememberstack.surfaces.cli.MemoryClient", _Factory)

    assert main(["query", "sql", "SELECT 1 AS n"]) == 0
    sql_out = json.loads(capsys.readouterr().out)
    assert sql_out["termination_reason"] == "completed"

    assert main(["query", "space", "--include-examples"]) == 0
    space_out = json.loads(capsys.readouterr().out)
    assert space_out["headline"] == TWO_LAYER_HEADLINE_FULL
    assert TWO_LAYER_HEADLINE_NOTE in space_out["headline"]
    assert "sql_grammar" in space_out

    assert (
        main(
            [
                "query",
                "run-saved",
                "examples",
                "relation_current",
                "--parameters",
                json.dumps([str(uuid4())]),
            ]
        )
        == 0
    )
    saved_out = json.loads(capsys.readouterr().out)
    assert saved_out["contract"] == "QueryResult/v1"
    assert saved_out["saved_query"]["name"] == "relation_current"


def test_core_prose_is_authority_for_cypher_and_claims_verbatim() -> None:
    """Discovery/skill and examples reuse core constants; manifest stays Batch E."""
    from rememberstack.core.open_query_prose import bound_worked_examples
    from rememberstack.core.open_query_prose import CLAIMS_VERBATIM_PURPOSE
    from rememberstack.core.open_query_prose import CLAIMS_VERBATIM_SQL
    from rememberstack.core.open_query_prose import NATIVE_CYPHER_TRAVERSAL_AGGREGATION
    from rememberstack.spine.query_space.manifest import load_manifest
    from rememberstack.surfaces.query_sandbox.examples import EXAMPLE_QUERIES

    assert "RELATES" in NATIVE_CYPHER_TRAVERSAL_AGGREGATION
    assert "count(" in NATIVE_CYPHER_TRAVERSAL_AGGREGATION.lower()
    examples = {str(item["key"]): item for item in bound_worked_examples()}
    assert len(examples) == 8
    assert examples["native_cypher_traversal_aggregation"]["body"] == (
        NATIVE_CYPHER_TRAVERSAL_AGGREGATION
    )
    assert examples["semantic_to_relational"]["body"] == CLAIMS_VERBATIM_SQL
    assert examples["semantic_to_relational"]["purpose"] == CLAIMS_VERBATIM_PURPOSE
    assert EXAMPLE_QUERIES["claims_verbatim"] == (
        CLAIMS_VERBATIM_PURPOSE,
        CLAIMS_VERBATIM_SQL,
    )
    # Hashed manifest keeps the Batch E node-list example (not the discovery
    # traversal body). Rolling the surface hash for first-call prose is out of
    # scope without the Batch E pending-revalidation protocol.
    cypher_entry = next(
        entry
        for entry in load_manifest()["hash_members"]["function_signatures"]["functions"]
        if entry["name"] == "query_cypher"
    )
    assert (
        cypher_entry["example"]
        == "MATCH (e:Entity) RETURN e.id, e.name ORDER BY e.name LIMIT 20"
    )
    assert cypher_entry["example"] != NATIVE_CYPHER_TRAVERSAL_AGGREGATION
    assert (
        load_manifest()["surface_manifest_hash"]
        == "6234117e1cf4897d6c31d634dc587deed1dd00a3b2f1d71de4a768b8078c2d21"
    )


# --- Batch F correction pass regressions ------------------------------------


def test_sql_max_rows_zero_means_zero_rows(migrated: str) -> None:
    """SQL max_rows=0 returns no rows and discloses row_cap=0 (matches Cypher)."""
    facade = _facade(migrated)
    outcome = facade.query_sql(sql="SELECT 1 AS n", max_rows=0)
    assert outcome.termination_reason == "completed", outcome.error_message
    assert outcome.returned_row_count == 0
    assert list(outcome.rows) == []
    assert outcome.limits.row_cap == 0


def test_saved_query_caller_max_rows_zero_override(migrated: str) -> None:
    """Caller max_rows=0 wins over a positive stored default on run_saved_query."""
    from rememberstack.surfaces.query_sandbox.saved_queries import SavedQueryVersion

    class _Stub:
        """Registry stub with a positive stored max_rows default."""

        @property
        def deployment_id(self) -> UUID:
            return _DEPLOYMENT

        def list_saved_queries(self, **_: object) -> tuple:
            return ()

        def describe_saved_query(self, **_: object) -> SavedQueryDescription:
            raise AssertionError("not used")

        def resolve(self, **_: object) -> SavedQueryVersion:
            return SavedQueryVersion(
                query_id=uuid4(),
                version=1,
                namespace="customer",
                name="limits_probe",
                sql="SELECT 1 AS n",
                query_hash="fixed-for-zero-rows-override",
                parameter_schema={},
                status="active",
                validated_surface_manifest_hash=_HASH,
                assurance="customer_authored",
                default_limits={"max_rows": 7},
            )

    facade = OpenQueryFacade(
        deployment_id=_DEPLOYMENT,
        sql=_sql_executor(migrated),
        saved_queries=_Stub(),  # type: ignore[arg-type]
    )
    outcome = facade.run_saved_query(
        namespace="customer", name="limits_probe", parameters=(), max_rows=0
    )
    assert outcome.termination_reason == "completed", outcome.error_message
    assert outcome.returned_row_count == 0
    assert outcome.limits.row_cap == 0
    assert outcome.saved_query is not None
    assert set(outcome.saved_query) == {
        "query_id",
        "namespace",
        "name",
        "version",
        "query_hash",
    }


def test_mcp_rejects_mixed_deployment_composition(migrated: str) -> None:
    """Local MCP refuses recipe surface + open-query facade from different deployments."""
    from rememberstack.adapters.testing import FakeModelProvider
    from rememberstack.spine import RecipeRegistry
    from rememberstack.spine import seed_canonical_recipes
    from rememberstack.surfaces import QueryEngine
    from rememberstack.surfaces import RecipeExecutor
    from rememberstack.surfaces import RecipeSurface

    engine = create_engine(migrated)
    other = uuid4()
    seed_canonical_recipes(
        registry=RecipeRegistry(engine=engine), deployment_id=_DEPLOYMENT
    )
    surface = RecipeSurface(
        registry=RecipeRegistry(engine=engine),
        executor=RecipeExecutor(
            query_engine=QueryEngine(
                engine=engine,
                search_index=_NullSearch(),
                model_provider=FakeModelProvider(),
                embedding_model="test/embed",
            )
        ),
        deployment_id=other,
    )
    with pytest.raises(ValueError, match="different deployment"):
        RecipeMcpServer(surface=surface, open_query=_facade(migrated))


def test_offline_gate_requires_zero_violations_on_both_arms() -> None:
    """Legacy-arm D41/D48/D54/cross-deployment violations fail the §8 gate."""
    metrics = {
        "legacy": {
            "success_rate": 80.0,
            "critical_categories": {"A": 90.0},
            "d41_violations": 1,
            "d48_violations": 0,
            "d54_violations": 0,
            "cross_deployment_violations": 0,
            "p95_latency_ms": 100.0,
            "metered_cost": 10.0,
            "invalid_sql_rate": 0.0,
            "invalid_cypher_rate": 0.0,
            "caps_and_drops_visible": True,
        },
        "open": {
            "success_rate": 81.0,
            "success_delta_lower_95": -1.0,
            "critical_categories": {"A": 88.0},
            "d41_violations": 0,
            "d48_violations": 0,
            "d54_violations": 0,
            "cross_deployment_violations": 0,
            "p95_latency_ms": 120.0,
            "metered_cost": 12.0,
            "invalid_sql_rate": 0.04,
            "invalid_cypher_rate": 0.03,
            "caps_and_drops_visible": True,
        },
    }
    report = evaluate_noninferiority(metrics=metrics)
    assert report["passed"] is False
    d41 = next(g for g in report["gates"] if g["name"] == "d41_violations")  # type: ignore[index]
    assert d41["passed"] is False
    assert d41["detail"]["legacy"] == 1
    assert d41["detail"]["open"] == 0
    assert d41["detail"]["required"] == 0


def test_mcp_rejects_explicit_null_for_string_and_integer_fields() -> None:
    """Present null is a type error for schema string/integer fields; omission defaults."""
    from rememberstack.surfaces.query_sandbox.mcp_tools import (
        validate_open_query_arguments,
    )

    with pytest.raises(SandboxRejection, match="pattern"):
        validate_open_query_arguments(
            name="describe_query_space", arguments={"pattern": None}
        )
    with pytest.raises(SandboxRejection, match="namespace"):
        validate_open_query_arguments(
            name="list_saved_queries", arguments={"namespace": None}
        )
    with pytest.raises(SandboxRejection, match="status"):
        validate_open_query_arguments(
            name="list_saved_queries", arguments={"status": None}
        )
    with pytest.raises(SandboxRejection, match="version"):
        validate_open_query_arguments(
            name="describe_saved_query",
            arguments={
                "namespace": "examples",
                "name": "relation_current",
                "version": None,
            },
        )
    with pytest.raises(SandboxRejection, match="version"):
        validate_open_query_arguments(
            name="run_saved_query",
            arguments={
                "namespace": "examples",
                "name": "relation_current",
                "version": None,
            },
        )
    with pytest.raises(SandboxRejection, match="max_rows"):
        validate_open_query_arguments(
            name="query_sql", arguments={"sql": "SELECT 1", "max_rows": None}
        )
    # Omission still applies defaults (not a type error).
    omitted = validate_open_query_arguments(name="describe_query_space", arguments={})
    assert omitted["pattern"] is None
    assert omitted["include_examples"] is False
    listed = validate_open_query_arguments(name="list_saved_queries", arguments={})
    assert listed["namespace"] is None
    assert listed["status"] is None


def test_http_explain_rejects_execution_only_fields(migrated: str) -> None:
    """SQL/Cypher explain routes 422 on max_rows/confirm; query routes still accept them."""
    app = _open_api(migrated)
    client = TestClient(app)

    sql_extra = client.post(
        "/query/sql/explain", json={"sql": "SELECT 1", "parameters": [], "max_rows": 1}
    )
    assert sql_extra.status_code == 422

    sql_ok = client.post(
        "/query/sql/explain", json={"sql": "SELECT 1", "parameters": []}
    )
    assert sql_ok.status_code == 200

    query_ok = client.post(
        "/query/sql", json={"sql": "SELECT 1 AS n", "parameters": [], "max_rows": 1}
    )
    assert query_ok.status_code == 200
    assert query_ok.json()["limits"]["row_cap"] == 1

    cypher_extra = client.post(
        "/query/cypher/explain",
        json={"cypher": "RETURN 1", "parameters": {}, "max_rows": 1, "confirm": True},
    )
    assert cypher_extra.status_code == 422

    cypher_confirm_only = client.post(
        "/query/cypher/explain",
        json={"cypher": "RETURN 1", "parameters": {}, "confirm": False},
    )
    assert cypher_confirm_only.status_code == 422


def test_http_auth_propagates_principal_to_open_execution_routes(migrated: str) -> None:
    """Authenticated open execution authenticates once and forwards principal."""
    from rememberstack.adapters.testing import FakeModelProvider
    from rememberstack.model import AuthenticatedContext
    from rememberstack.model import PerimeterCredential
    from rememberstack.spine import RecipeRegistry
    from rememberstack.spine import seed_canonical_recipes
    from rememberstack.surfaces import QueryEngine
    from rememberstack.surfaces import RecipeExecutor
    from rememberstack.surfaces import RecipeSurface

    seen: list[str | None] = []

    class _TrackingFacade:
        """Records principal on every execution-bearing open entry point."""

        def __init__(self, inner: OpenQueryFacade) -> None:
            """Wrap one facade and record principals on execution entry points."""
            self._inner = inner

        @property
        def deployment_id(self) -> UUID:
            """Forward the wrapped deployment identity."""
            return self._inner.deployment_id

        def query_sql(self, **kwargs: object) -> object:
            """Record principal then run sandboxed SQL."""
            seen.append(kwargs.get("principal"))  # type: ignore[arg-type]
            return self._inner.query_sql(**kwargs)  # type: ignore[arg-type]

        def explain_sql(self, **kwargs: object) -> object:
            """Record principal then explain sandboxed SQL."""
            seen.append(kwargs.get("principal"))  # type: ignore[arg-type]
            return self._inner.explain_sql(**kwargs)  # type: ignore[arg-type]

        def query_cypher(self, **kwargs: object) -> object:
            """Record principal then refuse Cypher (unavailable in this fixture)."""
            seen.append(kwargs.get("principal"))  # type: ignore[arg-type]
            raise SandboxRejection(
                code=QueryErrorCode.P2_UNAVAILABLE, message="no cypher in this proof"
            )

        def explain_cypher(self, **kwargs: object) -> object:
            """Record principal then refuse Cypher explain (unavailable here)."""
            seen.append(kwargs.get("principal"))  # type: ignore[arg-type]
            raise SandboxRejection(
                code=QueryErrorCode.P2_UNAVAILABLE, message="no cypher in this proof"
            )

        def describe_query_space(self, **kwargs: object) -> object:
            """Content-only discovery; do not invent a principal."""
            return self._inner.describe_query_space(**kwargs)  # type: ignore[arg-type]

        def search_query_space(self, **kwargs: object) -> object:
            """Content-only search; do not invent a principal."""
            return self._inner.search_query_space(**kwargs)  # type: ignore[arg-type]

        def list_saved_queries(self, **kwargs: object) -> object:
            """List saved queries without recording a principal."""
            return self._inner.list_saved_queries(**kwargs)  # type: ignore[arg-type]

        def describe_saved_query(self, **kwargs: object) -> object:
            """Describe a saved query without recording a principal."""
            return self._inner.describe_saved_query(**kwargs)  # type: ignore[arg-type]

        def run_saved_query(self, **kwargs: object) -> object:
            """Record principal then run a saved query."""
            seen.append(kwargs.get("principal"))  # type: ignore[arg-type]
            return self._inner.run_saved_query(**kwargs)  # type: ignore[arg-type]

    class _Auth:
        """Maps the good token to a distinct principal and counts authenticate calls."""

        def __init__(self) -> None:
            """Start with zero observed perimeter authentications."""
            self.authenticate_calls = 0

        def authenticate(
            self, *, credential: PerimeterCredential
        ) -> AuthenticatedContext:
            """Authenticate the bearer token and count the call."""
            self.authenticate_calls += 1
            if credential.value.get_secret_value() == b"good-token":
                return AuthenticatedContext(
                    deployment_id=_DEPLOYMENT, principal="alice-agent"
                )
            raise ValueError("unknown credential")

    engine = create_engine(migrated)
    seed_canonical_recipes(
        registry=RecipeRegistry(engine=engine), deployment_id=_DEPLOYMENT
    )
    query_engine = QueryEngine(
        engine=engine,
        search_index=_NullSearch(),
        model_provider=FakeModelProvider(),
        embedding_model="test/embed",
    )
    surface = RecipeSurface(
        registry=RecipeRegistry(engine=engine),
        executor=RecipeExecutor(query_engine=query_engine),
        deployment_id=_DEPLOYMENT,
    )
    tracking = _TrackingFacade(_facade(migrated))
    auth = _Auth()
    app = build_api(
        engine=query_engine,
        deployment_id=_DEPLOYMENT,
        admission=_OpenBoundary(),
        readiness=_OpenBoundary(),
        surface=surface,
        open_query=tracking,  # type: ignore[arg-type]
        auth=auth,  # type: ignore[arg-type]
    )
    client = TestClient(app)
    headers = {"Authorization": "Bearer good-token"}

    # One execution request must authenticate exactly once (shared perimeter dep).
    assert (
        client.post(
            "/query/sql",
            headers=headers,
            json={"sql": "SELECT 1 AS n", "parameters": []},
        ).status_code
        == 200
    )
    assert auth.authenticate_calls == 1
    assert seen == ["alice-agent"]

    assert (
        client.post(
            "/query/sql/explain",
            headers=headers,
            json={"sql": "SELECT 1", "parameters": []},
        ).status_code
        == 200
    )
    # Cypher is unavailable in this fixture; principal must still be forwarded.
    assert (
        client.post(
            "/query/cypher",
            headers=headers,
            json={"cypher": "RETURN 1", "parameters": {}},
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/query/cypher/explain",
            headers=headers,
            json={"cypher": "RETURN 1", "parameters": {}},
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/query/saved/examples/relation_current/run",
            headers=headers,
            json={"parameters": [str(uuid4())]},
        ).status_code
        == 200
    )
    # Discovery stays content-free: no principal recorded for space.
    assert client.get("/query/space", headers=headers).status_code == 200
    # Five execution routes + one discovery request, one auth each.
    assert auth.authenticate_calls == 6
    assert seen == ["alice-agent"] * 5


def test_selfhost_api_shares_kill_switches_audit_and_e1_embed_gate(
    migrated: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SelfHostProfile.api() shares kill/audit and binds the E1 generation gate."""
    from rememberstack.adapters.selfhost import LocalFSObjectStore
    from rememberstack.adapters.testing import FakeModelProvider
    from rememberstack.profiles.selfhost import SelfHostProfile
    from rememberstack.profiles.selfhost import SelfHostSettings
    from rememberstack.surfaces.query_sandbox.open_query import OpenQueryFacade

    # api() reports model bindings through OpenRouter settings; no network call.
    monkeypatch.setenv("REMEMBERSTACK_OPENROUTER_API_KEY", "test-key")

    lance_root = tmp_path / "lance"
    projection_work_root = tmp_path / "projection-work"
    graph_cache_root = tmp_path / "graph-cache"
    forget_manifest_root = tmp_path / "forget-manifests"
    objects_root = tmp_path / "objects"
    for path in (
        lance_root,
        projection_work_root,
        graph_cache_root,
        forget_manifest_root,
        objects_root,
    ):
        path.mkdir(parents=True, exist_ok=True)

    store = LocalFSObjectStore(root=objects_root)
    engine = create_engine(migrated)
    profile = SelfHostProfile(
        settings=SelfHostSettings(
            deployment_id=_DEPLOYMENT,
            lance_root=lance_root,
            projection_work_root=projection_work_root,
            graph_cache_root=graph_cache_root,
            forget_manifest_root=forget_manifest_root,
            migration_config=_ROOT / "alembic.ini",
        ),
        engine=engine,
        raw_store=store,  # type: ignore[arg-type]
        artifact_store=store,  # type: ignore[arg-type]
        corpusfs_store=store,  # type: ignore[arg-type]
        snapshot_store=store,  # type: ignore[arg-type]
        model_provider=FakeModelProvider(),  # type: ignore[arg-type]
    )

    captured: list[OpenQueryFacade] = []
    original_init = OpenQueryFacade.__init__

    def _capturing_init(self: OpenQueryFacade, **kwargs: object) -> None:
        """Record the facade that production self-host composition constructs."""
        original_init(self, **kwargs)  # type: ignore[misc]
        captured.append(self)

    monkeypatch.setattr(OpenQueryFacade, "__init__", _capturing_init)
    try:
        app = profile.api()
    finally:
        profile.close()

    assert app.title == "RememberStack query API"
    assert len(captured) == 1
    facade = captured[0]
    assert facade._cypher is not None  # noqa: SLF001
    # Production composition: one KillSwitches and one enabled AuditTrail for both.
    assert facade._sql._kills is facade._cypher._kills  # noqa: SLF001
    assert facade._sql._audit is facade._cypher._audit  # noqa: SLF001
    assert facade._sql._audit._enabled is True  # noqa: SLF001
    # Production-bound E1 embed partial rejects a foreign/pinned generation.
    embed = facade._sql._embed  # noqa: SLF001
    assert embed is not None
    with pytest.raises(SandboxRejection) as raised:
        embed(query="hello", embedder_generation="other/model")
    assert raised.value.code is QueryErrorCode.GENERATION_UNAVAILABLE


def _bound_headline_from_design_blockquote() -> str:
    """Derive the full two-layer headline from the binding design blockquote.

    The design is the authority; this returns paragraph + note with only the
    blockquote markers removed and hard-wrapped lines rejoined. No implementation
    constant is consulted.
    """
    design = (_ROOT / "plan/designs/open_query_space_design.md").read_text(
        encoding="utf-8"
    )
    marker = "**Bound two-layer retrieval headline (reused verbatim):**"
    start = design.index(marker) + len(marker)
    body_lines: list[str] = []
    for line in design[start:].lstrip("\n").splitlines():
        if line.startswith("> "):
            body_lines.append(line[2:])
        elif line == ">":
            body_lines.append("")
        elif line.startswith(">"):
            body_lines.append(line[1:])
        else:
            break
    paragraphs: list[str] = []
    current: list[str] = []
    for line in body_lines:
        if line == "":
            if current:
                paragraphs.append(" ".join(current))
                current = []
            continue
        current.append(line)
    if current:
        paragraphs.append(" ".join(current))
    return "\n\n".join(paragraphs)


def _normalize_headline_whitespace(text: str) -> str:
    """Collapse whitespace runs for defensible equality of prose copies."""
    return re.sub(r"\s+", " ", text.strip())


_OSS_HEADLINE_DOC_PATHS: tuple[Path, ...] = (
    _ROOT / "website/src/app/docs/concepts/page.mdx",
    _ROOT / "website/src/app/docs/mounts/page.mdx",
    _ROOT / "website/src/app/docs/reference/api/page.mdx",
    _ROOT / "website/src/app/docs/reference/cli/page.mdx",
    _ROOT / "website/src/app/docs/reference/mcp/page.mdx",
)


def test_bound_headline_matches_design_and_documentation_copies() -> None:
    """Design blockquote, discovery, and five OSS docs share the full headline."""
    from rememberstack.surfaces.query_sandbox.discovery import describe_query_space

    design_headline = _bound_headline_from_design_blockquote()
    # Preserve the design's backticks and D41/D54 note in the derived text.
    assert "`fact_claim_evidence`" in design_headline
    assert TWO_LAYER_HEADLINE_NOTE in design_headline

    discovery_headline = describe_query_space().headline
    assert "`fact_claim_evidence`" in discovery_headline
    assert TWO_LAYER_HEADLINE_NOTE in discovery_headline
    assert _normalize_headline_whitespace(design_headline) == (
        _normalize_headline_whitespace(discovery_headline)
    )

    design_norm = _normalize_headline_whitespace(design_headline)
    assert len(_OSS_HEADLINE_DOC_PATHS) == 5
    for path in _OSS_HEADLINE_DOC_PATHS:
        page = path.read_text(encoding="utf-8")
        assert "`fact_claim_evidence`" in page
        assert TWO_LAYER_HEADLINE_NOTE in page
        assert design_norm in _normalize_headline_whitespace(page)
