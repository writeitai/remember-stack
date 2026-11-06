"""Public provider artifacts must never hide validation or serialization semantics."""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Annotated
from typing import Any

from pydantic import AfterValidator
from pydantic import BaseModel
from pydantic import field_validator
from pydantic import model_serializer
import pytest


class PlainProvider(BaseModel):
    """A field-only shape which JSON Schema completely describes."""

    issuer: str


class ConstrainedProvider(PlainProvider):
    """A validator can tighten consumption without changing JSON Schema."""

    @field_validator("issuer")
    @classmethod
    def require_prefix(cls, value: str) -> str:
        """Illustrate a source parser requirement absent from its exported shape."""
        if not value.startswith("https://"):
            raise ValueError("secure issuer required")
        return value


class SerializedProvider(PlainProvider):
    """A serializer can change output even when field schemas look identical."""

    @model_serializer
    def serialize(self) -> dict[str, str]:
        """Illustrate a serialization change requiring explicit contract support."""
        return {"different": self.issuer}


class InitializedProvider(PlainProvider):
    """A post-init hook can refuse values outside declarative schema constraints."""

    def model_post_init(self, context: Any, /) -> None:
        """Illustrate a custom initialization requirement."""
        if not self.issuer:
            raise ValueError("issuer is empty")


def require_nonempty(value: str) -> str:
    """Illustrate annotated validation absent from the JSON Schema shape."""
    if not value:
        raise ValueError("nonempty issuer required")
    return value


class AnnotatedProvider(PlainProvider):
    """Functional field metadata can hide semantics just like decorators."""

    issuer: Annotated[str, AfterValidator(require_nonempty)]


@pytest.mark.parametrize(
    "model",
    [ConstrainedProvider, SerializedProvider, InitializedProvider, AnnotatedProvider],
)
def test_provider_semantics_need_explicit_contract_support(
    model: type[BaseModel], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A declaratively similar consumer cannot silently acquire hidden semantics."""
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "scripts"))
    exporter = importlib.import_module("export_typescript_contracts")
    exporter.require_schema_only_provider(model=PlainProvider)
    with pytest.raises(ValueError, match="requires|require"):
        exporter.require_schema_only_provider(model=model)
