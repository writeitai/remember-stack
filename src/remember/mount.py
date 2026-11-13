"""Thin foreground wrapper around mountable and remember.dev sessions (D155)."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
from threading import Thread
from typing import TextIO
from urllib.parse import quote
from uuid import UUID
from uuid import uuid4

import httpx
from pydantic import AwareDatetime
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import SecretStr
from pydantic import ValidationError

from remember.connection import Connection
from remember.connection import resolve_project
from remember.issuer import fetch_issuer_metadata


class _MountSession(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    session_id: str = Field(min_length=1)
    ticket: SecretStr = Field(min_length=1)
    expires_at: AwareDatetime


def _mountable() -> str:
    executable = shutil.which("mountable")
    if executable is None:
        raise ValueError(
            "mountable was not found on PATH; install it with "
            "`npm install --global --ignore-scripts mountable-cli` "
            "or `uv tool install mountable`"
        )
    return executable


def _create_session(*, connection: Connection, http: httpx.Client) -> _MountSession:
    claims = connection.claims
    if claims is None or not claims.org or connection.key is None:
        raise ValueError(
            "remember mount needs a remember.dev signed key with an organisation; "
            "run `remember login` (self-host: use any read-only S3 FUSE tool)"
        )
    key = connection.key.get_secret_value()
    metadata = fetch_issuer_metadata(claims.iss, http=http)
    project = connection.project
    try:
        project_id = UUID(project) if project else None
    except ValueError:
        project_id = None
    if project_id is None:
        resolved = resolve_project(key=key, claims=claims, project=project, http=http)
        project_id = UUID(resolved.project)
    base = metadata.endpoint("remember_account_endpoint").rstrip("/")
    response = http.post(
        f"{base}/v1/orgs/{quote(claims.org, safe='')}/mount-sessions",
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
        json={"project_id": str(project_id), "idempotency_key": str(uuid4())},
        follow_redirects=False,
    )
    if response.status_code not in (200, 201):
        hint = {
            401: "API key refused; run `remember login`",
            403: "the key needs memory:read on this project",
            404: "organisation or project not found, or mounts are unavailable",
        }.get(response.status_code, "could not create a mount session")
        # Never echo a response body: it may contain a ticket.
        raise ValueError(
            f"remember.dev mount session failed (HTTP {response.status_code}): {hint}"
        )
    try:
        return _MountSession.model_validate(response.json())
    except (ValueError, ValidationError) as error:
        raise ValueError("remember.dev returned an invalid mount session") from error


def _safe_line(line: str, ticket: str) -> str:
    """Redact even a ticket escaped inside a JSON diagnostic."""
    try:
        line = json.dumps(json.loads(line), ensure_ascii=False)
    except ValueError:
        return line.replace(ticket, "[redacted]").rstrip("\r\n")
    encoded_ticket = json.dumps(ticket, ensure_ascii=False)[1:-1]
    return line.replace(encoded_ticket, "[redacted]")


def _stderr(stream: TextIO, ticket: str) -> None:
    for line in stream:
        print(_safe_line(line, ticket), file=sys.stderr, flush=True)


def _exit_code(code: int) -> int:
    return code if code >= 0 else 128 - code


def mount(directory: str, *, connection: Connection, http: httpx.Client) -> int:
    """Report the matching mounted event immediately, then wait for unmount.

    Mountable owns the foreground mount's signals, renewals, cache and teardown.
    Keeping its pipes open until exit avoids detaching an unmonitored process.
    """
    executable = _mountable()  # Do not mint a ticket without an installed client.
    directory = str(Path(directory).resolve())
    session = _create_session(connection=connection, http=http)
    ticket = session.ticket.get_secret_value()
    mounted = False
    with subprocess.Popen(
        [executable, "mount", "--ticket-stdin", "--json", directory],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    ) as process:
        assert process.stdin is not None
        assert process.stdout is not None
        assert process.stderr is not None
        diagnostics = Thread(target=_stderr, args=(process.stderr, ticket), daemon=True)
        diagnostics.start()
        try:
            try:
                process.stdin.write(ticket + "\n")
                process.stdin.close()
            except BrokenPipeError:
                pass  # Read the child's own failure below.
            for line in process.stdout:
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except ValueError as error:
                    raise ValueError("mountable returned invalid JSON") from error
                if not isinstance(event, dict):
                    raise ValueError("mountable returned a non-object JSON event")
                if event.get("event") == "mounted":
                    if event.get("path") != directory:
                        continue
                    mounted = True
                    event["session_id"] = session.session_id
                print(_safe_line(json.dumps(event), ticket), flush=True)
            code = _exit_code(process.wait())
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            diagnostics.join()
    if code:
        return code
    if not mounted:
        raise ValueError(f"mountable exited without a mounted event for {directory}")
    return 0


def unmount(directory: str) -> int:
    """Delegate local unmount without credentials or a ticket."""
    result = subprocess.run(
        [_mountable(), "unmount", str(Path(directory).absolute())],
        stdin=subprocess.DEVNULL,
        check=False,
    )
    return _exit_code(result.returncode)
