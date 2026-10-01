"""Client-surface values that are safe in the dependency-light base install.

Backwards-compatibility re-export of models from remember.models and remember.errors.
"""

from __future__ import annotations

from remember.errors import ConnectorNotFoundError
from remember.models import ADJACENT_CHUNKS_MAX_WINDOW
from remember.models import ADJACENT_CHUNKS_MIN_WINDOW
from remember.models import AdjacentChunksRequest
from remember.models import CapabilityReadiness
from remember.models import ConnectorCreate
from remember.models import ConnectorDescriptor
from remember.models import DeploymentBuildInfo
from remember.models import DocumentDeletion
from remember.models import DocumentPage
from remember.models import DocumentPeopleMatch
from remember.models import DocumentSearchFilters
from remember.models import DocumentSearchPage
from remember.models import DocumentSearchPerson
from remember.models import DocumentSearchRequest
from remember.models import DocumentSearchResult
from remember.models import DocumentStatus
from remember.models import DocumentStatusFilter
from remember.models import DocumentSummary
from remember.models import DocumentVersionSummary
from remember.models import EffectiveInterval
from remember.models import MatchingEdition
from remember.models import PipelineReadinessReport
from remember.models import PipelineStageReadiness
from remember.models import ReadinessRequirements
from remember.models import ReadTime
from remember.models import SearchRequest
from remember.models import SECTION_HISTORY_DEFAULT_K
from remember.models import SECTION_HISTORY_MAX_K
from remember.models import SectionAmendment
from remember.models import SectionHistoryPage
from remember.models import SectionHistoryRequest
from remember.models import SectionHistoryRow
from remember.models import SectionHistorySection
from remember.models import ToolDescriptor
from remember.models import VersionPipelineReadiness

__all__ = (
    "ADJACENT_CHUNKS_MAX_WINDOW",
    "ADJACENT_CHUNKS_MIN_WINDOW",
    "AdjacentChunksRequest",
    "CapabilityReadiness",
    "ConnectorCreate",
    "ConnectorDescriptor",
    "ConnectorNotFoundError",
    "DeploymentBuildInfo",
    "DocumentDeletion",
    "DocumentPage",
    "DocumentPeopleMatch",
    "DocumentSearchFilters",
    "DocumentSearchPage",
    "DocumentSearchPerson",
    "DocumentSearchRequest",
    "DocumentSearchResult",
    "MatchingEdition",
    "ReadTime",
    "SectionHistoryPage",
    "SectionHistoryRequest",
    "SectionHistoryRow",
    "SectionHistorySection",
    "SectionAmendment",
    "EffectiveInterval",
    "DocumentStatus",
    "DocumentStatusFilter",
    "DocumentSummary",
    "DocumentVersionSummary",
    "PipelineReadinessReport",
    "PipelineStageReadiness",
    "ReadinessRequirements",
    "SearchRequest",
    "SECTION_HISTORY_DEFAULT_K",
    "SECTION_HISTORY_MAX_K",
    "ToolDescriptor",
    "VersionPipelineReadiness",
)
