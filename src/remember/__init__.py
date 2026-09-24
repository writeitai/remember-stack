"""remember: open memory infrastructure for AI agents.

The single canonical Python package for Remember, providing the lightweight client
SDK, the platform CLI, and the Model Context Protocol (MCP) server.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version

from remember.client import AccountApi
from remember.client import Client
from remember.client import Client as RememberClient
from remember.client import MemoryClient
from remember.connection import resolve_connection
from remember.errors import AccountApiUnavailable
from remember.errors import ConnectorNotFoundError
from remember.errors import MemoryApiError
from remember.errors import PipelineDeadLettered
from remember.errors import ProjectResolutionError
from remember.errors import RateLimited
from remember.errors import StoredKeyRefused
from remember.models import CapabilityReadiness
from remember.models import ClaimValidPrecision
from remember.models import ConnectorCreate
from remember.models import ConnectorDescriptor
from remember.models import ContextBundleV2
from remember.models import DocumentDeletion
from remember.models import DocumentPage
from remember.models import DocumentSearchFilters
from remember.models import DocumentSearchPage
from remember.models import DocumentSearchRequest
from remember.models import DocumentSearchResult
from remember.models import DocumentSummary
from remember.models import DocumentVersionSummary
from remember.models import Envelope
from remember.models import IngestedVersion
from remember.models import PipelineReadinessReport
from remember.models import PipelineStageReadiness
from remember.models import QueryResultDict
from remember.models import ReadinessRequirements
from remember.models import TemporalMatch
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
    "AccountApi",
    "AccountApiUnavailable",
    "CapabilityReadiness",
    "ClaimValidPrecision",
    "Client",
    "ConnectorCreate",
    "ConnectorDescriptor",
    "ConnectorNotFoundError",
    "ContextBundleV2",
    "DocumentDeletion",
    "DocumentPage",
    "DocumentSearchFilters",
    "DocumentSearchPage",
    "DocumentSearchRequest",
    "DocumentSearchResult",
    "DocumentSummary",
    "DocumentVersionSummary",
    "Envelope",
    "IngestedVersion",
    "MemoryApiError",
    "MemoryClient",
    "PipelineDeadLettered",
    "PipelineReadinessReport",
    "PipelineStageReadiness",
    "ProjectResolutionError",
    "QueryResultDict",
    "RateLimited",
    "ReadinessRequirements",
    "RememberClient",
    "StoredKeyRefused",
    "TemporalMatch",
    "ToolDescriptor",
    "VersionPipelineReadiness",
    "__version__",
    "resolve_connection",
)
