"""D143 processing layers: the configuration a deployment runs with.

A deployment processes three layers — sources, claims and facts. Sources is
always on; claims and facts can be switched off by the profile settings that
``setup`` writes to ``deployment_processing_layers``. A deployment without that
row is *unconfigured*: both layers are on and every code path is the one that
existed before D143.
"""

from typing import Final
from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict

from remember.models import ProcessingLayer

STANDARD_PROCESSING_PROFILE_ID: Final = "doc-text-standard-v1"
"""Measured profile of a version processed by all three layers (unchanged)."""

CLAIMS_PROCESSING_PROFILE_ID: Final = "doc-text-claims-v1"
"""Measured profile of a version processed by sources and claims only."""

SOURCES_PROCESSING_PROFILE_ID: Final = "doc-text-sources-v1"
"""Measured profile of a version processed by sources only."""

SwitchableLayer = Literal[ProcessingLayer.CLAIMS, ProcessingLayer.FACTS]


class LayerConfiguration(BaseModel):
    """Which layers a deployment processes and serves.

    ``configured`` is true once ``setup`` has written a configuration row; it
    selects the D143 boundary, ordering and guard paths. An unconfigured
    deployment has both layers enabled and takes today's paths everywhere.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    configured: bool
    claims_enabled: bool
    facts_enabled: bool

    def enabled(self, *, layer: ProcessingLayer) -> bool:
        """Whether the deployment processes and serves ``layer``."""
        if layer is ProcessingLayer.CLAIMS:
            return self.claims_enabled
        if layer is ProcessingLayer.FACTS:
            return self.facts_enabled
        return True

    @property
    def served(self) -> frozenset[ProcessingLayer]:
        """The layers retrieval may read: exactly the enabled ones."""
        return frozenset(
            layer for layer in ProcessingLayer if self.enabled(layer=layer)
        )

    @property
    def processing_profile_id(self) -> str:
        """The managed-meter profile a version measured now is processed under."""
        if self.facts_enabled:
            return STANDARD_PROCESSING_PROFILE_ID
        if self.claims_enabled:
            return CLAIMS_PROCESSING_PROFILE_ID
        return SOURCES_PROCESSING_PROFILE_ID


UNCONFIGURED: Final = LayerConfiguration(
    configured=False, claims_enabled=True, facts_enabled=True
)
"""The configuration of a deployment that has no configuration row."""


def profile_layers(*, processing_profile_id: str) -> frozenset[ProcessingLayer]:
    """The layers a measured processing profile requires (D143 §4.5)."""
    if processing_profile_id == SOURCES_PROCESSING_PROFILE_ID:
        return frozenset({ProcessingLayer.SOURCES})
    if processing_profile_id == CLAIMS_PROCESSING_PROFILE_ID:
        return frozenset({ProcessingLayer.SOURCES, ProcessingLayer.CLAIMS})
    return frozenset(ProcessingLayer)


class LayerAdmission(BaseModel):
    """Which of a read's channels it may consult, and what it must disclose (§7.2).

    ``unconsulted`` holds the read's channels left out because the deployment
    does not serve them or the request excluded them; ``backfilling`` holds
    consulted layers whose backfill has not finished. A read with either set
    non-empty never reports an empty answer as ``known_empty``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    consulted: frozenset[ProcessingLayer]
    unconsulted: frozenset[ProcessingLayer] = frozenset()
    backfilling: frozenset[ProcessingLayer] = frozenset()

    @property
    def complete(self) -> bool:
        """Every channel was consulted and none is still being backfilled."""
        return not self.unconsulted and not self.backfilling

    def admits(self, *, layer: ProcessingLayer) -> bool:
        """Whether the read may consult ``layer``."""
        return layer in self.consulted

    def boundary_explanation(self) -> str:
        """Why an empty answer is a boundary rather than ``known_empty``."""
        parts: list[str] = []
        if self.unconsulted:
            names = ", ".join(sorted(layer.value for layer in self.unconsulted))
            parts.append(
                f"the {names} layer(s) were not consulted: this deployment does"
                " not process them, or the request excluded them"
            )
        if self.backfilling:
            names = ", ".join(sorted(layer.value for layer in self.backfilling))
            parts.append(
                f"the {names} layer(s) are still being backfilled, so older"
                " versions may not be covered yet"
            )
        return "; ".join(parts)


class LayerConfigurationError(ValueError):
    """A configured layer set the dependency order forbids (facts without claims)."""


class LayerUnavailableError(Exception):
    """A read needs a processing layer the deployment does not serve (HTTP 409)."""

    def __init__(self, *, layer: ProcessingLayer, message: str) -> None:
        """Keep the layer so every surface can name it."""
        super().__init__(message)
        self.layer = layer


class LayerSelectionError(ValueError):
    """A ``layers`` selection the read cannot honour (HTTP 422).

    Raised when the selection excludes every channel of the read, or excludes a
    layer that one of its arguments requires.
    """

    def __init__(self, *, layer: ProcessingLayer | None, message: str) -> None:
        """Keep the excluded layer when one is to blame."""
        super().__init__(message)
        self.layer = layer
