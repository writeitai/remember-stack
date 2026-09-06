"""Backward-compatible model namespace forwarding to remember."""

import remember.models as _models
from remember import Envelope

__all__ = ["Envelope"]

for _attr in dir(_models):
    if not _attr.startswith("_") and _attr not in globals():
        globals()[_attr] = getattr(_models, _attr)
