"""The stock self-host profile exposes the complete implemented runtime shape."""

from pathlib import Path
import re
import subprocess
import sys

import pytest

from rememberstack.model import PipelineStage
from rememberstack.profiles.selfhost import _expected_components
from rememberstack.profiles.selfhost import _initialize_error_tracking
from rememberstack.profiles.selfhost import _model_bindings
from rememberstack.profiles.selfhost import _SUPPORTED_WORKER_STAGES

_ROOT = Path(__file__).resolve().parents[3]


def test_selfhost_setup_seeds_shipped_examples() -> None:
    """Self-host setup installs examples.* via the registry seed path, not Alembic."""
    from rememberstack.profiles import selfhost as selfhost_mod

    source = Path(selfhost_mod.__file__).read_text(encoding="utf-8")
    assert "seed_shipped_examples" in source
    assert "seed_canonical_operations" in source


def test_selfhost_assured_operations_share_the_live_graph_authority() -> None:
    """The D97 recipe and typed graph API share one bounded graph facade."""
    from rememberstack.profiles import selfhost as selfhost_mod

    source = Path(selfhost_mod.__file__).read_text(encoding="utf-8")
    assert "graph_queries = GraphQueries(" in source
    assert "graph_queries=graph_queries" in source
    assert "graph=graph_queries" in source


def test_selfhost_convert_uses_stock_passthrough_including_plain_text() -> None:
    """Stock convert must route text/plain (CLI .txt) as well as text/markdown."""
    from rememberstack.profiles import selfhost as selfhost_mod

    source = Path(selfhost_mod.__file__).read_text(encoding="utf-8")
    assert "stock_passthrough_routes" in source
    assert '{"text/markdown": MarkdownPassthroughConverter()}' not in source


def test_selfhost_composes_every_implemented_continuous_route() -> None:
    """Continuous handlers run; enum-only/fused stages do not get dummy workers."""
    assert _SUPPORTED_WORKER_STAGES == (
        PipelineStage.CONVERT,
        PipelineStage.STRUCTURE,
        PipelineStage.CHUNK,
        PipelineStage.EMBED_CHUNK,
        PipelineStage.EXTRACT_CLAIMS,
        PipelineStage.NORMALIZE_RELATIONS,
        PipelineStage.ADJUDICATE_OBSERVATIONS,
        PipelineStage.ADJUDICATE_SUPERSESSION,
        PipelineStage.EMBED_CLAIM,
        PipelineStage.RECONCILE,
        PipelineStage.LABEL_RELATION,
    )
    assert tuple(_expected_components()) == _SUPPORTED_WORKER_STAGES


def test_enum_only_and_fused_stages_are_not_advertised_as_workers() -> None:
    """A stage enum is not proof that an independently runnable handler exists."""
    assert {
        PipelineStage.GROUND_CLAIMS,
        PipelineStage.RESOLVE_ENTITIES,
        PipelineStage.EMBED_RELATION,
        PipelineStage.EMBED_OBSERVATION,
        PipelineStage.LABEL_OBSERVATION,
        PipelineStage.CROSSREF,
        PipelineStage.REFRESH_PROFILE,
    }.isdisjoint(_SUPPORTED_WORKER_STAGES)


def test_compose_wires_the_exact_supported_worker_set_and_projection_job() -> None:
    """Keep deployable Compose wiring in lockstep with the executable profile."""
    compose = (_ROOT / "compose.yaml").read_text(encoding="utf-8")
    composed_stages = tuple(
        PipelineStage(value)
        for value in re.findall(r'command: \["worker", "--stage", "([^"]+)"\]', compose)
    )

    assert composed_stages == _SUPPORTED_WORKER_STAGES
    assert 'profiles: ["operations"]' in compose
    assert 'command: ["project", "--plane", "p3"]' in compose
    for name in (
        "REMEMBERSTACK_SENTRY_DSN",
        "REMEMBERSTACK_SENTRY_ENVIRONMENT",
        "REMEMBERSTACK_SENTRY_SAMPLE_RATE",
        "LANGFUSE_PUBLIC_KEY",
        "LANGFUSE_SECRET_KEY",
        "LANGFUSE_HOST",
        "REMEMBERSTACK_SELFHOST_API_BEARER_BIND",
        "REMEMBERSTACK_SELFHOST_API_BEARER_TOKEN",
        "REMEMBERSTACK_SELFHOST_SPEND_LEASE_URL",
    ):
        assert f"{name}: ${{{name}:-}}" in compose
    for name, default in (
        ("REMEMBERSTACK_SELFHOST_GRAPH_POOL_SIZE", "4"),
        ("REMEMBERSTACK_SELFHOST_GRAPH_POOL_TIMEOUT_S", "1"),
        ("REMEMBERSTACK_SELFHOST_GRAPH_MAX_CONCURRENCY", "2"),
        ("REMEMBERSTACK_SELFHOST_GRAPH_WORK_MEM_KIB", "16384"),
    ):
        assert f"{name}: ${{{name}:-{default}}}" in compose
    assert (
        "REMEMBERSTACK_SELFHOST_REQUIRE_API_AUTH: "
        "${REMEMBERSTACK_SELFHOST_REQUIRE_API_AUTH:-false}" in compose
    )


def test_observability_imports_are_absent_without_environment_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default profile/benchmark imports do not load either optional SDK or shim."""
    for name in (
        "REMEMBERSTACK_SENTRY_DSN",
        "REMEMBERSTACK_SENTRY_ENVIRONMENT",
        "REMEMBERSTACK_SENTRY_SAMPLE_RATE",
        "LANGFUSE_PUBLIC_KEY",
        "LANGFUSE_SECRET_KEY",
        "LANGFUSE_HOST",
    ):
        monkeypatch.delenv(name, raising=False)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys;"
                " import rememberstack.profiles.selfhost;"
                " import benchmarks.locomo.runner;"
                " forbidden = {"
                "'sentry_sdk', 'langfuse',"
                " 'rememberstack.adapters.sentry',"
                " 'benchmarks.locomo.tracing'};"
                " loaded = forbidden.intersection(sys.modules);"
                " assert not loaded, sorted(loaded)"
            ),
        ],
        cwd=_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_sentry_environment_defaults_to_deployment_slug(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Self-host startup passes the deployment slug and default sample rate."""
    from rememberstack.adapters import sentry as sentry_adapter

    marker = object()
    calls: list[dict[str, object]] = []

    def initialize(**values: object) -> object:
        calls.append(values)
        return marker

    monkeypatch.setattr(sentry_adapter, "initialize_sentry", initialize)
    monkeypatch.setenv("REMEMBERSTACK_SENTRY_DSN", "https://public@example.test/1")
    monkeypatch.delenv("REMEMBERSTACK_SENTRY_ENVIRONMENT", raising=False)
    monkeypatch.setenv("REMEMBERSTACK_SENTRY_SAMPLE_RATE", "")

    telemetry = _initialize_error_tracking(
        command="worker", deployment_slug="customer-memory"
    )

    assert telemetry is marker
    assert calls == [
        {
            "dsn": "https://public@example.test/1",
            "environment": "customer-memory",
            "sample_rate": 1.0,
        }
    ]


def test_compose_forwards_the_dedicated_summary_seat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D66/D70: Compose and readiness expose the same flash-class binding."""
    compose = (_ROOT / "compose.yaml").read_text(encoding="utf-8")
    assert (
        "REMEMBERSTACK_SUMMARY_MODEL:"
        " ${REMEMBERSTACK_SUMMARY_MODEL:-z-ai/glm-4.7-flash}"
    ) in compose
    monkeypatch.delenv("REMEMBERSTACK_SUMMARY_MODEL", raising=False)
    monkeypatch.setenv("REMEMBERSTACK_OPENROUTER_API_KEY", "test-key")
    assert _model_bindings()["section_summary"] == "z-ai/glm-4.7-flash"


@pytest.mark.parametrize(
    ("configured", "reported"), (("nebius", "nebius"), ("", "auto"))
)
def test_model_bindings_report_embedding_provider_without_secrets(
    monkeypatch: pytest.MonkeyPatch, configured: str, reported: str
) -> None:
    """Readiness fingerprints the routing choice without exposing credentials."""
    monkeypatch.setenv("REMEMBERSTACK_OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("REMEMBERSTACK_OPENROUTER_EMBEDDING_PROVIDER", configured)

    bindings = _model_bindings()

    assert bindings["openrouter_embedding_provider"] == reported
    assert "test-key" not in bindings.values()


@pytest.mark.parametrize(
    ("configured", "reported"),
    (("nebius,deepinfra,siliconflow", "nebius,deepinfra,siliconflow"), ("", "unset")),
)
def test_model_bindings_report_embedding_provider_order(
    monkeypatch: pytest.MonkeyPatch, configured: str, reported: str
) -> None:
    """Readiness fingerprints the ordered shortlist without secrets."""
    compose = (_ROOT / "compose.yaml").read_text(encoding="utf-8")
    assert (
        "REMEMBERSTACK_OPENROUTER_EMBEDDING_PROVIDER_ORDER:"
        " ${REMEMBERSTACK_OPENROUTER_EMBEDDING_PROVIDER_ORDER:-}"
    ) in compose
    monkeypatch.setenv("REMEMBERSTACK_OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("REMEMBERSTACK_OPENROUTER_EMBEDDING_PROVIDER_ORDER", configured)

    bindings = _model_bindings()

    assert bindings["openrouter_embedding_provider_order"] == reported


@pytest.mark.parametrize(
    ("configured", "reported"), (("64000", "64000"), ("", "32000"))
)
def test_compose_and_model_bindings_expose_max_completion_tokens(
    monkeypatch: pytest.MonkeyPatch, configured: str, reported: str
) -> None:
    """Compose forwards the cap and readiness fingerprints its effective value."""
    compose = (_ROOT / "compose.yaml").read_text(encoding="utf-8")
    assert (
        "REMEMBERSTACK_OPENROUTER_MAX_COMPLETION_TOKENS:"
        " ${REMEMBERSTACK_OPENROUTER_MAX_COMPLETION_TOKENS:-}"
    ) in compose
    monkeypatch.setenv("REMEMBERSTACK_OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("REMEMBERSTACK_OPENROUTER_MAX_COMPLETION_TOKENS", configured)

    bindings = _model_bindings()

    assert bindings["openrouter_max_completion_tokens"] == reported


@pytest.mark.parametrize(("configured", "reported"), (("none", "none"), ("", "auto")))
def test_model_bindings_report_reasoning_effort(
    monkeypatch: pytest.MonkeyPatch, configured: str, reported: str
) -> None:
    """Readiness fingerprints the configured generation reasoning policy."""
    monkeypatch.setenv("REMEMBERSTACK_OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("REMEMBERSTACK_OPENROUTER_REASONING_EFFORT", configured)

    bindings = _model_bindings()

    assert bindings["openrouter_reasoning_effort"] == reported
