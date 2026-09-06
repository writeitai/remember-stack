"""Owner-only credential file for the remember CLI (D92).

Backwards-compatibility alias for remember.credentials.
"""

from __future__ import annotations

import sys

import remember.credentials as _mod
from remember.credentials import *  # noqa: F401, F403

sys.modules[__name__] = _mod
