"""Test-suite-wide isolation from the developer's local `.env`.

Importing `markitdown` pulls in `magika`, whose `__init__` calls
`dotenv.load_dotenv(dotenv.find_dotenv())` at import time. That walks up from the
working directory and loads this repository's `.env` into the process
environment for the whole pytest run — before any test executes, because
collection imports every test module.

Any `BaseSettings` constructed afterwards therefore inherits the developer's
local bindings. That made the suite pass in CI (no `.env` present) while failing
locally, which is the worst failure mode available: green where nobody is
looking and red where the work happens.

The optional pins below are the ones that change provider request payloads
rather than merely supplying a credential, so a leaked value silently alters what
a test asserts. Tests needing them set them explicitly with
`monkeypatch.setenv`, which runs after this fixture and therefore still wins.
"""

from __future__ import annotations

import pytest

#: Optional pins that alter provider request payloads when present.
_LEAKY_OPTIONAL_PINS = (
    "REMEMBERSTACK_OPENROUTER_EMBEDDING_PROVIDER",
    "REMEMBERSTACK_OPENROUTER_REASONING_EFFORT",
)


@pytest.fixture(autouse=True)
def isolate_optional_provider_pins(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove ambient optional provider pins for the duration of each test.

    Credentials and connection settings are deliberately left alone: the
    Postgres-gated tests rely on them being present.
    """
    for name in _LEAKY_OPTIONAL_PINS:
        monkeypatch.delenv(name, raising=False)
