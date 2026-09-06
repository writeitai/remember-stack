"""Device login OAuth flow.

Backwards-compatibility alias for remember.device_login.
"""

from __future__ import annotations

import sys

import remember.device_login as _mod
from remember.device_login import *  # noqa: F401, F403
from remember.device_login import _MAX_REDIRECTS  # noqa: F401
from remember.device_login import _POLL_MAX_REDIRECTS  # noqa: F401
from remember.device_login import _retry_after_seconds  # noqa: F401

sys.modules[__name__] = _mod
