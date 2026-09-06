"""The remember CLI: backwards-compatibility re-export."""

from __future__ import annotations

import httpx

import remember.cli as _rem_cli
from remember.cli import *  # noqa: F403
from remember.client import MemoryClient

_cli_memory_client = _rem_cli._cli_memory_client
_retry_pending_revocation = _rem_cli._retry_pending_revocation
_warn_if_expiring = _rem_cli._warn_if_expiring
operations_list = _rem_cli.operations_list
operations_run = _rem_cli.operations_run
_split_operation_arg = _rem_cli._split_operation_arg
main_status = _rem_cli.main_status
rememberstack_main = _rem_cli.rememberstack_main


def main(argv: list[str] | None = None) -> int:
    """Wrapper that synchronizes any test monkeypatches from this module to remember.cli."""
    for attr in (
        "httpx",
        "MemoryClient",
        "_cli_memory_client",
        "_retry_pending_revocation",
        "_warn_if_expiring",
        "operations_list",
        "operations_run",
        "_split_operation_arg",
    ):
        if attr in globals():
            val = globals()[attr]
            if getattr(_rem_cli, attr, None) is not val:
                setattr(_rem_cli, attr, val)
    return _rem_cli.main(argv)


__all__ = (
    "httpx",
    "MemoryClient",
    "_cli_memory_client",
    "_retry_pending_revocation",
    "_split_operation_arg",
    "_warn_if_expiring",
    "main",
    "main_status",
    "operations_list",
    "operations_run",
    "rememberstack_main",
)
