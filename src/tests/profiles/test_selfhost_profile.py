"""The stock self-host profile exposes the complete implemented runtime shape."""

from pathlib import Path
import re

import pytest

from rememberstack.model import PipelineStage
from rememberstack.profiles.selfhost import _expected_components
from rememberstack.profiles.selfhost import _model_bindings
from rememberstack.profiles.selfhost import _SUPPORTED_WORKER_STAGES

_ROOT = Path(__file__).resolve().parents[3]


def test_selfhost_composes_every_implemented_continuous_route() -> None:
    """Ten real handlers run; enum-only/fused stages do not get dummy workers."""
    assert _SUPPORTED_WORKER_STAGES == (
        PipelineStage.CONVERT,
        PipelineStage.STRUCTURE,
        PipelineStage.CHUNK,
        PipelineStage.EMBED_CHUNK,
        PipelineStage.EXTRACT_CLAIMS,
        PipelineStage.NORMALIZE_RELATIONS,
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
        PipelineStage.ADJUDICATE_OBSERVATIONS,
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
    assert 'command: ["project", "--plane", "all"]' in compose


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
