"""The dependency-light typed HTTP SDK (D62 client surface).

Backwards-compatibility re-export of remember.client.
"""

from __future__ import annotations

from remember.client import *  # noqa: F403
from remember.client import Client
from remember.client import ClientSettings
from remember.client import CloudClient
from remember.client import ExplicitEnvSettings
from remember.client import MemoryClient
from remember.client import QueryResultDict
from remember.errors import MemoryApiError

__all__ = (
    "Client",
    "ClientSettings",
    "CloudClient",
    "ExplicitEnvSettings",
    "MemoryApiError",
    "MemoryClient",
    "QueryResultDict",
)
