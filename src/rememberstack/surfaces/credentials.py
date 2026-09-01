"""Owner-only credential file for the ``remember`` CLI (D92).

``MemoryClient`` / ``ClientSettings`` must not import this module. Ambient
file pickup would send an embedded library to a host the caller never set.
"""

from __future__ import annotations

from datetime import datetime
import errno
import json
import os
from pathlib import Path
import stat
from typing import Literal
from uuid import UUID
from uuid import uuid4

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import SecretStr
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict


class CredentialError(ValueError):
    """The credential file cannot be read or written safely."""


class TokenHostSettings(BaseSettings):
    """CLI token-host and config-dir settings (``REMEMBERSTACK_`` prefix)."""

    model_config = SettingsConfigDict(env_prefix="REMEMBERSTACK_", extra="ignore")

    token_host: str | None = None
    config_dir: Path | None = None


class _XdgSettings(BaseSettings):
    """XDG base directory; not RememberStack-prefixed."""

    model_config = SettingsConfigDict(extra="ignore")

    xdg_config_home: Path | None = None


class CliClientEnv(BaseSettings):
    """Optional CLI URL/token env so defaults can fall through to the file."""

    model_config = SettingsConfigDict(env_prefix="REMEMBERSTACK_", extra="ignore")

    api_url: str | None = None
    api_authorization: SecretStr | None = None


class CredentialFile(BaseModel):
    """Version-1 credential document. Unknown version refuses to read.

    ``expires_at`` is optional because it was added to version 1 after version 1
    shipped, and because a control plane that predates expiring credentials does
    not send one. ``None`` therefore means *this credential has no recorded
    expiry* — not *it never expires*. The server is the authority either way;
    this field exists so the CLI can warn a human before a job fails at 3am.

    ``extra="forbid"`` is kept deliberately, even though it means a file written
    by a newer CLI is refused by an older one. This file is written by this
    program, so an unrecognised key really is corruption, and being told to log
    in again is a recoverable outcome — quietly ignoring a field that changes
    when the credential stops working is not.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    version: Literal[1]
    api_url: str
    token_host: str
    access_token: SecretStr
    token_type: Literal["Bearer"]
    token_id: UUID
    org_id: UUID
    deployment_id: UUID
    label: str
    token_prefix: str
    expires_at: datetime | None = None


def credentials_dir(*, settings: TokenHostSettings | None = None) -> Path:
    """Resolve the config directory: explicit, XDG, then ``~/.config``."""
    resolved = (
        settings if settings is not None else TokenHostSettings.model_validate({})
    )
    if resolved.config_dir is not None:
        return resolved.config_dir
    xdg = _XdgSettings.model_validate({}).xdg_config_home
    if xdg is not None:
        return xdg / "rememberstack"
    return Path.home() / ".config" / "rememberstack"


def credentials_path(*, settings: TokenHostSettings | None = None) -> Path:
    """Return the credentials.json path under the resolved directory."""
    return credentials_dir(settings=settings) / "credentials.json"


def load_credentials(
    *, settings: TokenHostSettings | None = None
) -> CredentialFile | None:
    """Load the file, or return ``None`` when it does not exist."""
    path = credentials_path(settings=settings)
    flags = os.O_RDONLY
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if nofollow:
        flags |= nofollow
    try:
        handle = os.open(path, flags)
    except FileNotFoundError:
        return None
    except OSError as error:
        if error.errno in {errno.ELOOP, getattr(errno, "EMLINK", errno.ELOOP)}:
            raise CredentialError("credentials path is a symlink") from error
        if error.errno == errno.ENOENT:
            return None
        raise CredentialError("credentials file is unreadable") from error
    try:
        mode = stat.S_IMODE(os.fstat(handle).st_mode)
        if mode & stat.S_IROTH:
            raise CredentialError("credentials file is world-readable")
        if mode & stat.S_IRGRP:
            raise CredentialError("credentials file is group-readable")
        with os.fdopen(handle, "r", encoding="utf-8") as stream:
            handle = -1
            try:
                return CredentialFile.model_validate_json(stream.read())
            except Exception as error:
                raise CredentialError("credentials file is unreadable") from error
    finally:
        if handle >= 0:
            os.close(handle)


def write_credentials(
    *, credential: CredentialFile, settings: TokenHostSettings | None = None
) -> Path:
    """Write ``credentials.json`` with ``0600`` from the first byte."""
    directory = credentials_dir(settings=settings)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    path = directory / "credentials.json"
    if path.exists() and path.is_symlink():
        raise CredentialError("credentials path is a symlink")
    temporary = directory / f".credentials.{uuid4().hex}.tmp"
    dumped = credential.model_dump(mode="json")
    dumped["access_token"] = credential.access_token.get_secret_value()
    payload = json.dumps(dumped, separators=(",", ":"), sort_keys=True)
    handle = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        if temporary.exists():
            temporary.unlink()
        raise
    return path


def unlink_credentials(*, settings: TokenHostSettings | None = None) -> None:
    """Remove the credential file when present. Leaves a missing file alone."""
    path = credentials_path(settings=settings)
    if path.exists() and path.is_symlink():
        raise CredentialError("credentials path is a symlink")
    if path.exists():
        path.unlink()


def authorization_header(*, token: str) -> str:
    """Accept a raw secret or a complete ``Bearer …`` value."""
    stripped = token.strip()
    if stripped.lower().startswith("bearer "):
        return stripped
    return f"Bearer {stripped}"
