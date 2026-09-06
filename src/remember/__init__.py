"""remember: open memory infrastructure for AI agents.

The single canonical Python package for Remember, providing the lightweight client
SDK, the platform CLI, and the Model Context Protocol (MCP) server.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version

from remember.client import Client
from remember.client import ClientSettings
from remember.client import CloudClient
from remember.client import MemoryClient
from remember.client import MemoryClient as RememberClient
from remember.errors import CloudError
from remember.errors import ConnectorNotFoundError
from remember.errors import MemoryApiError
from remember.errors import NotPermitted
from remember.errors import RateLimited
from remember.errors import Unauthenticated
from remember.models import BillingStatus
from remember.models import CapabilityReadiness
from remember.models import ConnectorCreate
from remember.models import ConnectorDescriptor
from remember.models import ContextBundleV1
from remember.models import Deployment
from remember.models import Envelope
from remember.models import IngestedVersion
from remember.models import LedgerEntry
from remember.models import PipelineReadinessReport
from remember.models import PipelineStageReadiness
from remember.models import QueryResultDict
from remember.models import ReadinessRequirements
from remember.models import SpendGate
from remember.models import ToolDescriptor
from remember.models import VersionPipelineReadiness

try:
    __version__ = version("remember")
except PackageNotFoundError:
    try:
        __version__ = version("rememberstack")
    except PackageNotFoundError:
        __version__ = "0.0.0+uninstalled"

__all__ = (
    "BillingStatus",
    "CapabilityReadiness",
    "Client",
    "ClientSettings",
    "CloudClient",
    "CloudError",
    "ConnectorCreate",
    "ConnectorDescriptor",
    "ConnectorNotFoundError",
    "ContextBundleV1",
    "Deployment",
    "Envelope",
    "IngestedVersion",
    "LedgerEntry",
    "MemoryApiError",
    "MemoryClient",
    "NotPermitted",
    "PipelineReadinessReport",
    "PipelineStageReadiness",
    "QueryResultDict",
    "RateLimited",
    "ReadinessRequirements",
    "RememberClient",
    "SpendGate",
    "ToolDescriptor",
    "Unauthenticated",
    "VersionPipelineReadiness",
    "__version__",
)
