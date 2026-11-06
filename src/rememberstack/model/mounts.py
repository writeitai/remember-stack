"""Portable result of publishing the four D51 read-only mount surfaces."""

from typing import Annotated
from typing import Literal
from uuid import UUID

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

_MountLocator = Annotated[str, Field(min_length=1)]


class PublishedMounts(BaseModel):
    """Locations for the P3, artifact, raw and (when configured) Plane-K views.

    ``knowledge`` is absent unless a Plane-K checkout root was supplied: an
    always-empty placeholder directory would only mislead a browsing agent.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    deployment_id: UUID
    p3: _MountLocator
    artifacts: _MountLocator
    raw: _MountLocator
    knowledge: _MountLocator | None = None
    read_only: Literal[True]
