"""The owner-only credential file and pending-revocation journal (D136 §8.4).

``credentials.json`` (version 2) holds one key: a signed key from an issuer
(``remember login``) or a self-hosted shared secret beside its engine URL
(``remember setup --self-hosted``). The SDK and the CLI both read it, after
explicit arguments and the environment (:mod:`remember.connection`).

``pending-revocation.json`` records keys that were replaced but whose
revocation the issuer has not yet confirmed, so a crash can never strand a live
key with nothing on the machine naming it.

Both files are written owner-only (``0600`` in a ``0700`` directory) through a
temporary file, ``fsync`` and an atomic rename followed by a directory
``fsync``; both refuse symlinks and group- or world-readable modes.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
import errno
import json
import os
from pathlib import Path
import stat
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import field_validator
from pydantic import model_validator
from pydantic import SecretStr
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict


class CredentialError(ValueError):
    """The credential file or journal cannot be read or written safely."""


class DurabilityUnconfirmed(Exception):
    """The file was written and renamed, but the rename's durability is unknown.

    Deliberately not a :class:`CredentialError`: the new file *is* in place, so a
    caller must not unwind (for example by revoking the key the file now
    names). It reports the weaker guarantee and carries on.
    """


class _ConfigDirSettings(BaseSettings):
    """``REMEMBER_CONFIG_DIR`` and ``XDG_CONFIG_HOME``."""

    model_config = SettingsConfigDict(extra="ignore")

    remember_config_dir: Path | None = None
    xdg_config_home: Path | None = None


def config_dir() -> Path:
    """``REMEMBER_CONFIG_DIR``, else ``$XDG_CONFIG_HOME/remember``, else ``~/.config/remember``."""
    settings = _ConfigDirSettings.model_validate({})
    if settings.remember_config_dir is not None:
        return settings.remember_config_dir
    if settings.xdg_config_home is not None:
        return settings.xdg_config_home / "remember"
    return Path.home() / ".config" / "remember"


def credentials_path() -> Path:
    """The ``credentials.json`` path under :func:`config_dir`."""
    return config_dir() / "credentials.json"


def pending_revocation_path() -> Path:
    """The journal path beside the credential file."""
    return config_dir() / "pending-revocation.json"


class StoredCredentials(BaseModel):
    """``credentials.json`` version 2: one key.

    - An **issuer key** (``remember login``): ``issuer`` and ``key`` are set,
      ``api_url`` is not — the engine is resolved from the key.
    - A **self-hosted entry** (``remember setup --self-hosted``): ``api_url``
      is set, ``issuer`` is not, and ``key`` is the engine's shared secret (or
      absent for an engine without authentication).

    ``extra="forbid"``: this program writes the file, so an unknown field is
    corruption, and "run ``remember login``" is a recoverable answer.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    version: Literal[2]
    issuer: str | None = None
    key: SecretStr | None = None
    key_id: str | None = None
    expires_at: datetime | None = None
    default_project: str | None = Field(default=None, min_length=1, max_length=200)
    api_url: str | None = None

    @field_validator("expires_at")
    @classmethod
    def _expiry_must_be_absolute(cls, value: datetime | None) -> datetime | None:
        """Refuse a naive expiry rather than guessing its timezone."""
        if value is not None and value.tzinfo is None:
            raise ValueError("expires_at must carry a timezone offset")
        return value

    @model_validator(mode="after")
    def _one_shape(self) -> StoredCredentials:
        """An issuer key or a self-hosted entry, never both, never neither."""
        if self.issuer is not None:
            if self.key is None or self.api_url is not None:
                raise ValueError("an issuer entry holds a key and no api_url")
        elif self.api_url is None:
            raise ValueError("a self-hosted entry needs api_url")
        return self


def load_credentials() -> StoredCredentials | None:
    """Read the file, or ``None`` when it does not exist."""
    raw = _read_owner_only(path=credentials_path(), label="credentials file")
    if raw is None:
        return None
    try:
        return StoredCredentials.model_validate_json(raw)
    except ValueError as error:
        raise CredentialError(
            f"{credentials_path()} is not a version-2 credential file; "
            "run `remember login`"
        ) from error


def write_credentials(*, credentials: StoredCredentials) -> None:
    """Replace ``credentials.json`` atomically, owner-only from the first byte.

    Raises :class:`DurabilityUnconfirmed` when the rename happened but the
    directory could not be synced.
    """
    dumped = credentials.model_dump(mode="json")
    if credentials.key is not None:
        dumped["key"] = credentials.key.get_secret_value()
    _write_owner_only(path=credentials_path(), payload=dumped)


def confirm_credentials_durable() -> None:
    """Sync ``credentials.json`` and its directory again.

    Reading the file back proves it is visible, not that it survives a crash.
    A replaced key is revoked only after this succeeds, so a power loss can
    never leave the machine holding a revoked key. Raises
    :class:`DurabilityUnconfirmed` on failure.
    """
    path = credentials_path()
    try:
        handle = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        handle = -1
    except OSError as error:
        raise DurabilityUnconfirmed(f"{path} could not be opened ({error})") from error
    if handle >= 0:
        try:
            os.fsync(handle)
        except OSError as error:
            raise DurabilityUnconfirmed(
                f"{path} could not be synced ({error})"
            ) from error
        finally:
            os.close(handle)
    _fsync_directory(path.parent)


def unlink_credentials() -> None:
    """Remove the credential file. A missing file is success."""
    path = credentials_path()
    if path.is_symlink():
        raise CredentialError("credentials path is a symlink")
    path.unlink(missing_ok=True)


class PendingRevocation(BaseModel):
    """One replaced key whose revocation the issuer has not yet confirmed."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    issuer: str
    key: SecretStr
    key_id: str | None = None


class PendingRevocations(BaseModel):
    """Every key still awaiting revocation, oldest first."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    version: Literal[2]
    entries: tuple[PendingRevocation, ...] = ()


#: The most keys that may await revocation at once. Every entry is retried
#: under the credential lock, so an unbounded journal would stall every
#: command; twenty is far more than occasional logins accumulate.
MAX_PENDING_REVOCATIONS = 20


def load_pending_revocations() -> PendingRevocations:
    """Read the journal; an empty one when nothing is outstanding.

    A journal that will not parse is kept and reported, never discarded: it
    may be the only record of a live key.
    """
    path = pending_revocation_path()
    raw = _read_owner_only(path=path, label="pending-revocation file")
    if raw is None:
        return PendingRevocations(version=2)
    try:
        return PendingRevocations.model_validate_json(raw)
    except ValueError as error:
        raise CredentialError(
            f"{path} is unreadable; it may name keys that are still live, so "
            "it is kept — revoke them with your issuer and delete the file"
        ) from error


def journal_key(*, entry: PendingRevocation) -> None:
    """Durably add one key to the journal (idempotent for the same key)."""
    journal = load_pending_revocations()
    secret = entry.key.get_secret_value()
    if any(item.key.get_secret_value() == secret for item in journal.entries):
        return
    if len(journal.entries) >= MAX_PENDING_REVOCATIONS:
        raise CredentialError(
            f"{len(journal.entries)} keys are already awaiting revocation; "
            "revoke them with your issuer before logging in again"
        )
    _write_journal(entries=(*journal.entries, entry))


def forget_journalled_key(*, key: str) -> None:
    """Drop the entry for one key, keeping every other entry."""
    journal = load_pending_revocations()
    remaining = tuple(
        item for item in journal.entries if item.key.get_secret_value() != key
    )
    if len(remaining) != len(journal.entries):
        _write_journal(entries=remaining)


def _write_journal(*, entries: tuple[PendingRevocation, ...]) -> None:
    """Replace the journal atomically; remove it when it becomes empty."""
    path = pending_revocation_path()
    if not entries:
        if path.is_symlink():
            raise CredentialError("pending-revocation path is a symlink")
        path.unlink(missing_ok=True)
        _fsync_directory(path.parent)
        return
    payload = {
        "version": 2,
        "entries": [
            {**item.model_dump(mode="json"), "key": item.key.get_secret_value()}
            for item in entries
        ],
    }
    _write_owner_only(path=path, payload=payload)


def _read_owner_only(*, path: Path, label: str) -> str | None:
    """Read a secret-holding file without following symlinks; ``None`` if absent."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        handle = os.open(path, flags)
    except FileNotFoundError:
        return None
    except OSError as error:
        if error.errno == errno.ELOOP:
            raise CredentialError(f"{label} path is a symlink") from error
        raise CredentialError(f"{label} is unreadable ({error})") from error
    with os.fdopen(handle, "r", encoding="utf-8") as stream:
        mode = stat.S_IMODE(os.fstat(stream.fileno()).st_mode)
        if mode & (stat.S_IRGRP | stat.S_IROTH):
            raise CredentialError(f"{label} {path} is readable by other users")
        return stream.read()


def _write_owner_only(*, path: Path, payload: dict[str, object]) -> None:
    """Temporary file (``0600``), fsync, atomic rename, directory fsync."""
    directory = path.parent
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    if path.is_symlink():
        raise CredentialError(f"{path} is a symlink")
    temporary = directory / f".{path.stem}.{uuid4().hex}.tmp"
    text = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    handle = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    _fsync_directory(directory)


def _fsync_directory(directory: Path) -> None:
    """Make a rename durable.

    A filesystem that cannot sync a directory at all (Windows; ``EINVAL`` or
    ``ENOTSUP``) is accepted silently — nothing can be confirmed there. Any
    other failure raises :class:`DurabilityUnconfirmed`: the rename happened,
    but a crash could still undo it.
    """
    if os.name == "nt":  # pragma: no cover - Windows has no directory fsync
        return
    try:
        handle = os.open(directory, getattr(os, "O_DIRECTORY", os.O_RDONLY))
        try:
            os.fsync(handle)
        finally:
            os.close(handle)
    except OSError as error:
        unsupported = {
            errno.EINVAL,
            errno.ENOTSUP,
            getattr(errno, "EOPNOTSUPP", errno.ENOTSUP),
        }
        if error.errno in unsupported:
            return
        raise DurabilityUnconfirmed(
            f"{directory} could not be synced ({error}); the file is written "
            "but a crash could undo it"
        ) from error


@contextmanager
def credential_lock() -> Iterator[None]:
    """Serialise the commands that change the credential file or journal.

    Two concurrent logins would each mint a key and overwrite the other's
    file. Being unable to take the lock is a refusal, never an unlocked run.
    """
    directory = config_dir()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    handle = os.open(directory / ".lock", os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        try:
            import fcntl
        except ImportError:  # pragma: no cover - Windows
            import msvcrt

            try:
                msvcrt.locking(handle, msvcrt.LK_LOCK, 1)  # type: ignore[attr-defined]
            except OSError as error:
                raise CredentialError(
                    f"the credential lock could not be taken ({error})"
                ) from error
            try:
                yield
            finally:
                os.lseek(handle, 0, os.SEEK_SET)
                msvcrt.locking(handle, msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
            return
        try:
            fcntl.flock(handle, fcntl.LOCK_EX)
        except OSError as error:
            raise CredentialError(
                f"the credential lock could not be taken ({error}); another "
                "`remember login` or `logout` may be running"
            ) from error
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
    finally:
        os.close(handle)
