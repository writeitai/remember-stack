"""RememberStack: open memory infrastructure for AI agents."""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version

try:
    __version__ = version("remember")
except PackageNotFoundError:
    try:
        __version__ = version("rememberstack")
    except PackageNotFoundError:  # running from a checkout without installation
        __version__ = "0.0.0+uninstalled"
