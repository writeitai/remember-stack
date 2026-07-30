"""Typed environment for STATE-Bench adapter arms (pydantic-settings only)."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict

from benchmarks.state_bench.protocol import DEFAULT_RECIPE_NAME


class RememberStackArmSettings(BaseSettings):
    """Config for RememberStackMemoryAgent (prefix RS_STATE_)."""

    model_config = SettingsConfigDict(env_prefix="RS_STATE_", extra="ignore")

    recipe: str = Field(default=DEFAULT_RECIPE_NAME)
    fail_closed: bool = Field(
        default=True,
        description="If true, infrastructure errors re-raise after recording.",
    )


class Bm25ArmSettings(BaseSettings):
    """Config for Bm25MemoryAgent."""

    model_config = SettingsConfigDict(env_prefix="RS_STATE_", extra="ignore")

    documents_json: Path
    domain: str | None = None
