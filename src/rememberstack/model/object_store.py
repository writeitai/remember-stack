"""Provider-neutral identities for stored raw, artifact and corpus objects."""

from typing import Annotated

from pydantic import ConfigDict
from pydantic import Field
from pydantic import RootModel


class ObjectKey(RootModel[Annotated[str, Field(min_length=1)]]):
    """Non-empty key in the configured object store."""

    model_config = ConfigDict(frozen=True)


class ObjectAlreadyExistsError(Exception):
    """A write to an occupied key — objects are immutable, never replaced."""


class ObjectKeyEscapesRootError(Exception):
    """An object key that would resolve outside the store root — refused."""
