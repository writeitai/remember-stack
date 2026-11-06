"""Explicit composition root package."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rememberstack.profiles.selfhost import EngineSettings
    from rememberstack.profiles.selfhost import run_task
    from rememberstack.profiles.selfhost import SelfHostProfile
    from rememberstack.profiles.selfhost import SelfHostSettings

__all__ = ("EngineSettings", "SelfHostProfile", "SelfHostSettings", "run_task")


def __getattr__(name: str) -> object:
    """Load a profile only when its explicit composition root is requested."""
    if name in __all__:
        from rememberstack.profiles import selfhost

        return getattr(selfhost, name)
    raise AttributeError(name)
