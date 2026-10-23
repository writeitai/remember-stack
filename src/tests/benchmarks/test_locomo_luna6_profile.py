"""Prove the requested model roles match the actual composed ingestion settings."""

from pathlib import Path

from benchmarks.locomo.protocol import PROTOCOL_REGISTRY
from benchmarks.locomo.sharding import store_backup
import pytest

from rememberstack.adapters import OpenRouterModelProvider
from rememberstack.adapters import OpenRouterSettings
from rememberstack.model import ModelRequest
from rememberstack.profiles.selfhost import _expected_components
from rememberstack.profiles.selfhost import _model_bindings


def test_luna_profile_matches_composed_models_and_versions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The profile must describe executable bindings, including Jev, rather than labels."""
    protocol = PROTOCOL_REGISTRY["full-v39"]
    profile = protocol.execution_profile
    assert profile is not None
    for name, value in profile.ingest_environment.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("REMEMBERSTACK_OPENROUTER_API_KEY", "offline-test")
    assert _model_bindings() == profile.ingest_model_bindings
    assert {
        stage.value: version for stage, version in _expected_components().items()
    } == profile.ingest_component_versions
    provider = OpenRouterModelProvider(settings=OpenRouterSettings.model_validate({}))
    assert (
        provider._reasoning_effort_for(
            request=ModelRequest(model=protocol.answer_agent_model, prompt="ingestion")
        )
        == "low"
    )
    assert (
        provider._reasoning_effort_for(
            request=ModelRequest(
                model=protocol.answer_agent_model,
                prompt="answer",
                reasoning_effort=protocol.answer_agent_reasoning_effort,
            )
        )
        == "high"
    )
    assert provider._chat_provider_payload() == {
        "only": ["openai/flex"],
        "allow_fallbacks": False,
        "data_collection": "deny",
    }
    assert protocol.judge_reasoning_effort == "high"
    assert (
        protocol.judge_prompt_template
        == PROTOCOL_REGISTRY["full-v39"].judge_prompt_template
    )


def test_backup_preserves_jev_engine_and_flex_instead_of_dormant_prompt_slot(
    tmp_path: Path,
) -> None:
    """A new-profile runtime replay must retain adjudication engine and routing."""
    import json

    profile = PROTOCOL_REGISTRY["full-v39"].execution_profile
    assert profile is not None
    (tmp_path / "run.json").write_text(
        json.dumps({"execution_profile": profile.model_dump(mode="json")})
    )
    (tmp_path / "state.json").write_text(
        json.dumps(
            {
                "readiness": {
                    "fixture": {"model_bindings": profile.ingest_model_bindings}
                }
            }
        )
    )
    environment = store_backup._runtime_environment(
        run_dir=tmp_path, sample_id="fixture"
    )
    assert environment["REMEMBERSTACK_FACT_ADJUDICATION_ENGINE"] == "jev"
    assert environment["REMEMBERSTACK_TYPESAFE_MODEL"] == "jev-1.13.0"
    assert environment["REMEMBERSTACK_FACT_FALLBACK_TO_PROMPT"] == "false"
    assert environment["REMEMBERSTACK_OPENROUTER_CHAT_PROVIDER_ONLY"] == "openai/flex"
