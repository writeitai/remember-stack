"""Public dependency-light client API."""

from remember.client import MemoryClient
from remember.errors import MemoryApiError
from rememberstack.model.client import CapabilityReadiness
from rememberstack.model.client import ConnectorCreate
from rememberstack.model.client import ConnectorDescriptor
from rememberstack.model.client import ConnectorNotFoundError
from rememberstack.model.client import PipelineReadinessReport
from rememberstack.model.client import PipelineStageReadiness
from rememberstack.model.client import ReadinessRequirements
from rememberstack.model.client import ToolDescriptor
from rememberstack.model.client import VersionPipelineReadiness

__all__ = (
    "CapabilityReadiness",
    "ConnectorCreate",
    "ConnectorDescriptor",
    "ConnectorNotFoundError",
    "MemoryApiError",
    "MemoryClient",
    "PipelineReadinessReport",
    "PipelineStageReadiness",
    "ReadinessRequirements",
    "ToolDescriptor",
    "VersionPipelineReadiness",
)
