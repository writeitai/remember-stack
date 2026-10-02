"""D140 opened-handle checks refuse nonregular credentials without blocking."""

import os
from pathlib import Path

import pytest

from remember.credentials import _read_owner_only
from remember.credentials import CredentialError


@pytest.mark.skipif(os.name == "nt", reason="POSIX FIFO and permission contract")
def test_credential_fifo_is_refused_without_a_writer(tmp_path: Path) -> None:
    """O_NONBLOCK plus fstat refuses a FIFO before any secret-file read."""
    path = tmp_path / "credentials.json"
    os.mkfifo(path, mode=0o600)
    with pytest.raises(CredentialError, match="regular file"):
        _read_owner_only(path=path, label="credential fixture")


def test_credential_directory_is_refused(tmp_path: Path) -> None:
    """Directories cannot be parsed as credential files on any platform."""
    path = tmp_path / "credentials.json"
    path.mkdir()
    with pytest.raises(CredentialError):
        _read_owner_only(path=path, label="credential fixture")
