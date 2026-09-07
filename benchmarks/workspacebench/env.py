"""Allowlisted subprocess environments. Never copies credential files."""

from __future__ import annotations

from collections.abc import Mapping
import os

from benchmarks.workspacebench.errors import WorkspaceBenchError

# Executables, TLS, locale, HOME, and Remember config-dir discovery.
# Ambient CODEX_HOME is never copied: live Codex runs force a disposable home.
# Credential values stay in owner-controlled stores; this adapter does not read
# those files and does not pass token/password/secret variables through.
_ALLOWED_ENV_KEYS = frozenset(
    {
        "PATH",
        "HOME",
        "USER",
        "LOGNAME",
        "SHELL",
        "TMPDIR",
        "TMP",
        "TEMP",
        "LANG",
        "LANGUAGE",
        "LC_ALL",
        "LC_CTYPE",
        "LC_MESSAGES",
        "LC_NUMERIC",
        "LC_TIME",
        "LC_COLLATE",
        "TZ",
        "TERM",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_CACHE_HOME",
        "REMEMBER_CONFIG_DIR",
        "REMEMBERSTACK_CONFIG_DIR",
    }
)
_ALLOWED_ENV_PREFIXES = ("LC_",)
_SECRET_KEY_MARKERS = (
    "token",
    "authorization",
    "password",
    "secret",
    "api_key",
    "apikey",
    "openai_api_key",
    "bearer",
)


def sanitized_subprocess_env(
    *, extra: Mapping[str, str] | None = None
) -> dict[str, str]:
    """Return a copy of allowed non-secret variables for spawned processes.

    Reads the parent environment only to copy the allowlisted keys. Does not
    read, copy, or parse ``credentials.json`` or ``auth.json``.
    """
    source = os.environ  # noqa: TID251 -- subprocess allowlist, not adapter config
    env: dict[str, str] = {}
    for key, value in source.items():
        if _is_allowed_env_key(key) and isinstance(value, str):
            env[key] = value
    if extra:
        for key, value in extra.items():
            if _is_secret_env_key(key) or (
                isinstance(value, str) and value.lower().startswith("bearer ")
            ):
                raise WorkspaceBenchError(
                    f"subprocess extra env must not pass credential {key}"
                )
            if _is_allowed_env_key(key):
                env[key] = value
    return env


def _is_allowed_env_key(key: str) -> bool:
    if _is_secret_env_key(key):
        return False
    if key in _ALLOWED_ENV_KEYS:
        return True
    return any(key.startswith(prefix) for prefix in _ALLOWED_ENV_PREFIXES)


def _is_secret_env_key(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in _SECRET_KEY_MARKERS)


__all__ = ("sanitized_subprocess_env",)
