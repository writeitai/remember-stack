"""Deprecated rememberstack namespace forwarding to remember."""

import warnings

warnings.warn(
    "The 'rememberstack' PyPI distribution is deprecated as of v0.17.0. "
    "Please migrate to 'remember' (pip install remember) for the Python SDK and platform CLI, "
    "or use the official Docker image 'ghcr.io/writeitai/remember-stack' for self-hosted engine deployments. "
    "See https://remember.dev/docs/project-status/ for details.",
    DeprecationWarning,
    stacklevel=2,
)

import remember
from remember import (
    Client,
    CloudClient,
    Envelope,
    MemoryClient,
    PipelineReadinessReport,
    ReadinessRequirements,
    RememberClient,
    ToolDescriptor,
)

__all__ = [
    "Client",
    "CloudClient",
    "Envelope",
    "MemoryClient",
    "PipelineReadinessReport",
    "ReadinessRequirements",
    "RememberClient",
    "ToolDescriptor",
]

for _attr in dir(remember):
    if not _attr.startswith("_") and _attr not in globals():
        globals()[_attr] = getattr(remember, _attr)
