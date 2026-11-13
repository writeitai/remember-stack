"""Provider-neutral identities for stored raw, artifact and corpus objects."""

from typing import Annotated
from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import RootModel


class ObjectKey(RootModel[Annotated[str, Field(min_length=1)]]):
    """Non-empty key in the configured object store."""

    model_config = ConfigDict(frozen=True)


class ObjectPurgeEvidence(BaseModel):
    """Whether S3 unfinished uploads were checked or left to provider cleanup.

    ``provider_managed`` records unsupported listing, not verified absence of
    unfinished uploads. Published objects are still deleted and verified.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    multipart_uploads: Literal["checked", "provider_managed"]


class ObjectAlreadyExistsError(Exception):
    """A write to an occupied key — objects are immutable, never replaced."""


class ObjectKeyEscapesRootError(Exception):
    """An object key that would resolve outside the store root — refused."""
