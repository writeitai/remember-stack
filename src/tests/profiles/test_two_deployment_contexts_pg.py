"""D144: two deployments' contexts in one process, each over its own database.

A host that serves several deployments builds one profile per deployment
from that deployment's own settings, overrides the API's
``deployment_context`` per request, and runs named tasks with ``run_task``.
This proves the three seams together keep every row in its own database.
"""

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from uuid import UUID
from uuid import uuid4

from alembic import command
from alembic.config import Config
from fastapi import Request
from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.engine import make_url

from remember.mcp_tools import ADJACENT_CHUNKS_TOOL_NAME
from rememberstack.adapters import OpenRouterSettings
from rememberstack.adapters.selfhost import LocalFSObjectStore
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import PipelineStage
from rememberstack.model import RunResultOutcome
from rememberstack.profiles import run_task
from rememberstack.profiles import SelfHostProfile
from rememberstack.profiles import SelfHostSettings
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine.settings import load_database_settings
from rememberstack.surfaces.http_api import ApiDeployment
from rememberstack.surfaces.http_api import deployment_context

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_A = UUID("d1440000-0000-0000-0000-00000000000a")
_DEPLOYMENT_B = UUID("d1440000-0000-0000-0000-00000000000b")


class _NoModels:
    """Converting Markdown calls no model; any call is a test failure."""

    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"unexpected model-provider call {name}")


@pytest.fixture(scope="module")
def database_url() -> str:
    """The configured server; the scratch databases are created beside it."""
    try:
        return load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip("REMEMBERSTACK_DATABASE_URL is required for the context proof")


@pytest.fixture(scope="module")
def engines(database_url: str) -> Iterator[dict[UUID, Engine]]:
    """Two fresh databases at head, one per deployment, dropped afterwards."""
    names = {
        _DEPLOYMENT_A: f"rs_ctx_a_{uuid4().hex[:8]}",
        _DEPLOYMENT_B: f"rs_ctx_b_{uuid4().hex[:8]}",
    }
    admin = create_engine(database_url, isolation_level="AUTOCOMMIT")
    created: dict[UUID, Engine] = {}
    try:
        for deployment_id, name in names.items():
            with admin.connect() as connection:
                # the identifier is generated here, never caller input
                connection.execute(text(f'CREATE DATABASE "{name}"'))
            url = (
                make_url(database_url)
                .set(database=name)
                .render_as_string(hide_password=False)
            )
            config = Config(str(_ROOT / "alembic.ini"))
            config.set_main_option("sqlalchemy.url", url)
            command.upgrade(config=config, revision="head")
            created[deployment_id] = create_engine(url)
            DeploymentBootstrapper(engine=created[deployment_id]).bootstrap_deployment(
                deployment_input=DeploymentBootstrapInput(
                    deployment_id=deployment_id,
                    slug=name,
                    name=name,
                    default_language="en",
                    raw_bucket="mem://raw",
                    artifacts_bucket="mem://artifacts",
                    corpusfs_bucket="mem://corpusfs",
                ),
                sole_deployment=True,
            )
        yield created
    finally:
        for engine in created.values():
            engine.dispose()
        with admin.connect() as connection:
            for name in names.values():
                connection.execute(
                    text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
                )
                # Per-database roles are cluster-global; remove this run's.
                for prefix in ("rememberstack_query_", "rememberstack_graph_"):
                    connection.execute(text(f'DROP ROLE IF EXISTS "{prefix}{name}"'))
        admin.dispose()


@pytest.fixture
def profiles(
    engines: dict[UUID, Engine], tmp_path: Path
) -> Iterator[dict[UUID, SelfHostProfile]]:
    """One context per deployment, each with its own database and stores."""
    built: dict[UUID, SelfHostProfile] = {}
    for deployment_id, engine in engines.items():
        root = tmp_path / str(deployment_id)
        (root / "forget").mkdir(parents=True)
        built[deployment_id] = SelfHostProfile(
            settings=SelfHostSettings(
                deployment_id=deployment_id,
                forget_manifest_root=root / "forget",
                projection_work_root=root / "projection",
            ),
            engine=engine,
            raw_store=LocalFSObjectStore(root=root / "raw"),  # type: ignore[arg-type]
            artifact_store=LocalFSObjectStore(root=root / "artifacts"),  # type: ignore[arg-type]
            corpusfs_store=LocalFSObjectStore(root=root / "corpusfs"),  # type: ignore[arg-type]
            model_provider=_NoModels(),  # type: ignore[arg-type]
            # Each deployment's own provider account; nothing from the env.
            model_provider_settings=OpenRouterSettings(api_key=f"key-{deployment_id}"),
        )
    yield built
    # The engines belong to the module fixture and outlive these profiles.


def _rows(engine: Engine, sql: str) -> list[tuple[object, ...]]:
    with engine.connect() as connection:
        return [tuple(row) for row in connection.execute(text(sql))]


def test_two_contexts_keep_requests_and_tasks_in_their_own_database(
    engines: dict[UUID, Engine], profiles: dict[UUID, SelfHostProfile]
) -> None:
    """Requests and tasks for A and B, interleaved, never touch the other's rows."""
    contexts: dict[str, ApiDeployment] = {
        str(deployment_id): profile.api_deployment()
        for deployment_id, profile in profiles.items()
    }

    def per_request(request: Request) -> ApiDeployment:
        return contexts[request.headers["x-deployment"]]

    # One app for both deployments: composed from A, resolved per request.
    app = profiles[_DEPLOYMENT_A].api()
    app.dependency_overrides[deployment_context] = per_request
    client = TestClient(app)

    for deployment_id, body in (
        (_DEPLOYMENT_B, b"# Bravo\n\nOnly B holds this.\n"),
        (_DEPLOYMENT_A, b"# Alpha\n\nOnly A holds this.\n"),
    ):
        response = client.post(
            "/ingest",
            params={"filename": "note.md", "mime": "text/markdown"},
            content=body,
            headers={
                "x-deployment": str(deployment_id),
                "content-type": "application/octet-stream",
            },
        )
        assert response.status_code == 200, response.text

    for deployment_id, engine in engines.items():
        assert _rows(engine, "SELECT DISTINCT deployment_id FROM documents") == [
            (deployment_id,)
        ]
        listed = client.get("/documents", headers={"x-deployment": str(deployment_id)})
        assert listed.status_code == 200, listed.text
        assert len(listed.json()["documents"]) == 1

    # A request's deployment without a capability neither serves nor
    # advertises it, even though the composing deployment has it.
    contexts["read-only"] = replace(contexts[str(_DEPLOYMENT_A)], ingest=None)
    described = client.get("/deployment", headers={"x-deployment": "read-only"})
    assert described.status_code == 200, described.text
    assert "ingest" not in described.json()["tools"]
    # Adjacent chunks needs only the engine, so it stays advertised.
    no_operations = "no-operations"
    contexts[no_operations] = replace(contexts[str(_DEPLOYMENT_A)], surface=None)
    tools = client.get("/deployment", headers={"x-deployment": no_operations}).json()[
        "tools"
    ]
    assert ADJACENT_CHUNKS_TOOL_NAME in tools
    assert "facts_context" not in tools
    assert (
        "ingest"
        in client.get(
            "/deployment", headers={"x-deployment": str(_DEPLOYMENT_A)}
        ).json()["tools"]
    )
    refused = client.post(
        "/ingest",
        params={"filename": "note.md", "mime": "text/markdown"},
        content=b"# Refused\n",
        headers={
            "x-deployment": "read-only",
            "content-type": "application/octet-stream",
        },
    )
    assert refused.status_code == 404

    pending = {
        deployment_id: _rows(
            engine,
            "SELECT processing_id FROM processing_state"
            f" WHERE stage = '{PipelineStage.CONVERT.value}'",
        )
        for deployment_id, engine in engines.items()
    }
    task_a = pending[_DEPLOYMENT_A][0][0]
    task_b = pending[_DEPLOYMENT_B][0][0]
    assert isinstance(task_a, UUID)
    assert isinstance(task_b, UUID)

    # A context cannot see, let alone run, another database's task.
    missing = run_task(profiles[_DEPLOYMENT_A], task_b)
    assert missing.outcome is RunResultOutcome.NO_WORK

    # Interleaved: B's task, then A's, each through its own context.
    for deployment_id, task in ((_DEPLOYMENT_B, task_b), (_DEPLOYMENT_A, task_a)):
        result = run_task(profiles[deployment_id], task)
        assert result.processing_id == task
        assert result.outcome is RunResultOutcome.SUCCEEDED
    # Running a finished task again finds nothing due.
    assert run_task(profiles[_DEPLOYMENT_A], task_a).outcome is (
        RunResultOutcome.NO_WORK
    )

    for deployment_id, engine in engines.items():
        assert _rows(engine, "SELECT DISTINCT deployment_id FROM processing_state") == [
            (deployment_id,)
        ]
        assert _rows(
            engine,
            "SELECT status::text FROM processing_state"
            f" WHERE stage = '{PipelineStage.CONVERT.value}'",
        ) == [("succeeded",)]
        # The chain continued inside the same database.
        assert _rows(
            engine,
            "SELECT count(*) FROM processing_state"
            f" WHERE stage = '{PipelineStage.STRUCTURE.value}'",
        ) == [(1,)]
