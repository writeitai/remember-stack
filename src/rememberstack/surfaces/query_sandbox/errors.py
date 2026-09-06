"""The exhaustive public error taxonomy for the open query space.

Backwards-compatibility re-export of remember.query_sandbox.errors.
"""

from __future__ import annotations

from remember.query_sandbox.errors import *  # noqa: F403
from remember.query_sandbox.errors import QueryErrorCode
from remember.query_sandbox.errors import SandboxRejection

__all__ = ("QueryErrorCode", "SandboxRejection")
