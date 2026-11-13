"""D155 managed mounts: fake HTTP and a real, fake mountable on PATH, no network."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import time
from uuid import UUID

import httpx
from pydantic import SecretStr
import pytest

import remember.cli as cli
from remember.credentials import StoredCredentials
from remember.credentials import write_credentials
from tests.surfaces.fake_issuer import ISSUER
from tests.surfaces.fake_issuer import make_key

_PROJECT = "b24f9363-4f38-48f6-9fad-8ad28fa9a912"
_OTHER_PROJECT = "f25e917d-7fe6-4332-84ea-ad718f599a9c"
_TICKET = "mtbltk_only-on-stdin_opaque-secret"
_SESSION = "session-123"


class _Cloud:
    """Issuer discovery and account API, deliberately separate from the engine."""

    def __init__(self) -> None:
        self.key = make_key(projects=[_PROJECT, _OTHER_PROJECT])
        self.requests: list[httpx.Request] = []
        self.status = 201
        self.body: object = {
            "session_id": _SESSION,
            "ticket": _TICKET,
            "expires_at": "2030-01-01T00:05:00Z",
        }

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/.well-known/oauth-authorization-server":
            return httpx.Response(
                200,
                json={
                    "issuer": ISSUER,
                    "remember_account_endpoint": "https://account.test/api",
                    "remember_project_endpoint": f"{ISSUER}/api/v1/keys/self/project",
                },
            )
        assert request.headers["Authorization"] == f"Bearer {self.key}"
        if request.url.path == "/api/v1/keys/self/project":
            assert request.method == "GET"
            return httpx.Response(
                200,
                json={
                    "project": _PROJECT,
                    "name": "docs",
                    "api_url": "https://engine.test",
                },
            )
        assert str(request.url) == "https://account.test/api/v1/orgs/o1/mount-sessions"
        assert request.method == "POST"
        return httpx.Response(self.status, json=self.body)

    def sessions(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == "POST"]


@pytest.fixture()
def cloud(monkeypatch: pytest.MonkeyPatch) -> _Cloud:
    cloud = _Cloud()
    monkeypatch.setenv("REMEMBER_API_KEY", cloud.key)
    monkeypatch.setenv("REMEMBER_PROJECT", _PROJECT)
    monkeypatch.setattr(
        cli, "_issuer_http", lambda: httpx.Client(transport=httpx.MockTransport(cloud))
    )
    return cloud


@pytest.fixture()
def mountable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[[str], Path]:
    """Run subprocesses, recording the test-only ticket, argv and environment."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    monkeypatch.setenv("PATH", str(bin_dir))
    evidence = tmp_path / "invocation.json"

    def install(code: str = "") -> Path:
        executable = bin_dir / "mountable"
        executable.write_text(
            f"#!{sys.executable}\n"
            "import json, os, sys\n"
            "from pathlib import Path\n"
            "ticket = sys.stdin.read()\n"
            f"Path({str(evidence)!r}).write_text(json.dumps(\n"
            "    {'argv': sys.argv[1:], 'env': dict(os.environ), 'stdin': ticket}))\n"
            "directory = sys.argv[-1]\n"
            "def emit(**event):\n"
            "    print(json.dumps(event), flush=True)\n"
            + (
                code
                or "emit(event='mounted', path=directory, filesystem_id='fs-1', mode='ro')\n"
            ),
            encoding="utf-8",
        )
        executable.chmod(0o755)
        return evidence

    return install


@pytest.mark.parametrize("status", [200, 201])
def test_mount_stdin_only_ticket_and_matching_event(
    cloud: _Cloud,
    mountable: Callable[[str], Path],
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    status: int,
) -> None:
    cloud.status = status
    evidence = mountable(
        "emit(event='starting')\n"
        "emit(event='mounted', path='/another/mount')\n"
        "emit(event='mounted', path=directory, filesystem_id='fs-1', mode='ro')\n"
        "emit(event='unmounted', reason='unmount', detail='')\n"
    )
    # Shell metacharacters stay in a single argv element; symlinks/relative paths
    # match mountable's canonical absolute mounted path.
    directory = tmp_path / "corpus ; $(touch shell-was-used)"
    directory.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(directory, target_is_directory=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("REMEMBER_API_URL", "https://engine-override.test")

    assert cli.main(["mount", "alias"]) == 0
    call = json.loads(evidence.read_text())
    assert call["argv"] == [
        "mount",
        "--ticket-stdin",
        "--json",
        str(directory.resolve()),
    ]
    assert call["stdin"] == _TICKET + "\n"
    assert _TICKET not in json.dumps(call["argv"])
    assert _TICKET not in json.dumps(call["env"])
    out, err = capfd.readouterr()
    events = [json.loads(line) for line in out.splitlines()]
    assert [e["event"] for e in events] == ["starting", "mounted", "unmounted"]
    assert events[1] == {
        "event": "mounted",
        "path": str(directory.resolve()),
        "filesystem_id": "fs-1",
        "mode": "ro",
        "session_id": _SESSION,
    }
    assert err == ""
    assert _TICKET not in out + err
    assert not (tmp_path / "shell-was-used").exists()
    body = json.loads(cloud.sessions()[0].content)
    assert body["project_id"] == _PROJECT
    assert UUID(body["idempotency_key"]).version == 4
    assert set(body) == {"project_id", "idempotency_key"}


def test_each_invocation_has_a_fresh_idempotency_key(
    cloud: _Cloud, mountable: Callable[[str], Path], tmp_path: Path
) -> None:
    mountable("")
    assert cli.main(["mount", str(tmp_path)]) == 0
    assert cli.main(["mount", str(tmp_path)]) == 0
    keys = [
        json.loads(request.content)["idempotency_key"] for request in cloud.sessions()
    ]
    assert len(set(keys)) == 2


@pytest.mark.parametrize("selection", [None, "docs"])
def test_default_or_named_project_uses_existing_resolver(
    cloud: _Cloud,
    mountable: Callable[[str], Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    selection: str | None,
) -> None:
    mountable("")
    monkeypatch.delenv("REMEMBER_PROJECT")
    argv = ["mount", str(tmp_path)]
    if selection is not None:
        argv += ["--project", selection]
    assert cli.main(argv) == 0
    resolution = next(r for r in cloud.requests if r.url.path.endswith("/project"))
    assert dict(resolution.url.params) == ({"project": selection} if selection else {})
    assert json.loads(cloud.sessions()[0].content)["project_id"] == _PROJECT


def test_stored_credentials_and_explicit_overrides(
    cloud: _Cloud,
    mountable: Callable[[str], Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mountable("")
    monkeypatch.delenv("REMEMBER_API_KEY")
    monkeypatch.delenv("REMEMBER_PROJECT")
    write_credentials(
        credentials=StoredCredentials(
            version=2,
            issuer=ISSUER,
            key=SecretStr(cloud.key),
            default_project=_OTHER_PROJECT,
        )
    )
    assert cli.main(["mount", str(tmp_path)]) == 0
    assert json.loads(cloud.sessions()[-1].content)["project_id"] == _OTHER_PROJECT
    monkeypatch.setenv("REMEMBER_PROJECT", _PROJECT)
    assert cli.main(["mount", str(tmp_path)]) == 0
    assert json.loads(cloud.sessions()[-1].content)["project_id"] == _PROJECT
    cloud.key = make_key(jti="explicit", projects=[_OTHER_PROJECT])
    monkeypatch.setenv("REMEMBER_API_KEY", "wrong-environment-key")
    assert (
        cli.main(
            [
                "mount",
                str(tmp_path),
                "--api-key",
                cloud.key,
                "--project",
                _OTHER_PROJECT,
            ]
        )
        == 0
    )
    assert json.loads(cloud.sessions()[-1].content)["project_id"] == _OTHER_PROJECT


@pytest.mark.parametrize("command", ["mount", "unmount"])
def test_missing_executable_precedes_any_ticket_request(
    cloud: _Cloud,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
    command: str,
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    assert cli.main([command, str(tmp_path)]) == 1
    assert cloud.requests == []
    out, err = capfd.readouterr()
    assert out == ""
    assert "mountable was not found on PATH" in err
    assert "npm install --global --ignore-scripts mountable-cli" in err
    assert "uv tool install mountable" in err


@pytest.mark.parametrize(
    ("status", "message"),
    [
        (401, "API key refused"),
        (403, "memory:read"),
        (404, "not found"),
        (503, "could not create"),
        (307, "could not create"),
    ],
)
def test_http_errors_do_not_start_mountable_or_print_response_secrets(
    cloud: _Cloud,
    mountable: Callable[[str], Path],
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
    status: int,
    message: str,
) -> None:
    evidence = mountable("")
    cloud.status = status
    cloud.body = {"detail": _TICKET}
    assert cli.main(["mount", str(tmp_path)]) == 1
    assert not evidence.exists()
    out, err = capfd.readouterr()
    assert out == ""
    assert f"HTTP {status}" in err
    assert message in err
    assert _TICKET not in err


@pytest.mark.parametrize(
    "body",
    [
        None,
        [],
        {"ticket": _TICKET},
        {"session_id": _SESSION, "ticket": _TICKET, "expires_at": _TICKET},
        {"session_id": _SESSION, "ticket": "", "expires_at": "2030-01-01T00:05:00Z"},
        {
            "session_id": _SESSION,
            "ticket": _TICKET + "\n",
            "expires_at": "2030-01-01T00:05:00Z",
        },
        {
            "session_id": _SESSION,
            "ticket": _TICKET + "\rmore",
            "expires_at": "2030-01-01T00:05:00Z",
        },
    ],
)
def test_invalid_session_response_never_prints_ticket(
    cloud: _Cloud,
    mountable: Callable[[str], Path],
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
    body: object,
) -> None:
    evidence = mountable("")
    cloud.body = body
    assert cli.main(["mount", str(tmp_path)]) == 1
    assert not evidence.exists()
    out, err = capfd.readouterr()
    assert "invalid mount session" in err
    assert _TICKET not in out + err


@pytest.mark.parametrize("key", [None, "self-host-shared-secret"])
def test_mount_requires_a_cloud_key_without_network(
    cloud: _Cloud,
    mountable: Callable[[str], Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
    key: str | None,
) -> None:
    evidence = mountable("")
    monkeypatch.delenv("REMEMBER_API_KEY")
    if key:
        monkeypatch.setenv("REMEMBER_API_KEY", key)
    assert cli.main(["mount", str(tmp_path)]) == 1
    assert not evidence.exists()
    assert cloud.requests == []
    assert "remember login" in capfd.readouterr().err


@pytest.mark.parametrize("code", [1, 2, 17])
def test_failed_or_expired_ticket_surfaces_mountable_error_and_exit_code(
    cloud: _Cloud,
    mountable: Callable[[str], Path],
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
    code: int,
) -> None:
    cloud.body = {
        "session_id": _SESSION,
        "ticket": _TICKET,
        "expires_at": "2000-01-01T00:05:00Z",
    }
    mountable(
        "emit(error={'code': 'ticket_invalid', 'message': 'ticket expired or used'})\n"
        "print('mountable: ticket expired or used', file=sys.stderr)\n"
        f"sys.exit({code})\n"
    )
    assert cli.main(["mount", str(tmp_path)]) == code
    out, err = capfd.readouterr()
    assert "ticket_invalid" in out
    assert "ticket expired or used" in err
    assert "session_id" not in out
    assert _TICKET not in out + err


def test_child_failure_after_mount_is_not_reported_as_success(
    cloud: _Cloud,
    mountable: Callable[[str], Path],
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
) -> None:
    mountable(
        "emit(event='mounted', path=directory)\n"
        "emit(event='unmounted', reason='session_ended', detail='session revoked')\n"
        "sys.exit(1)\n"
    )
    assert cli.main(["mount", str(tmp_path)]) == 1
    out, err = capfd.readouterr()
    assert _SESSION in out
    assert "session revoked" in out
    assert _TICKET not in out + err


@pytest.mark.parametrize(
    "script",
    [
        "",
        "emit(event='mounted', path='/wrong')\n",
        "print('not JSON')\n",
        "print('[]')\n",
    ],
)
def test_no_matching_mounted_event_is_a_failure(
    cloud: _Cloud,
    mountable: Callable[[str], Path],
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
    script: str,
) -> None:
    mountable(script or "pass\n")
    assert cli.main(["mount", str(tmp_path)]) == 1
    out, err = capfd.readouterr()
    assert "mountable" in err
    assert "session_id" not in out


def test_redacts_ticket_in_child_json_and_plain_diagnostics(
    cloud: _Cloud,
    mountable: Callable[[str], Path],
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
) -> None:
    mountable(
        "emit(error={'message': ticket.strip()})\n"
        "print(ticket.strip(), file=sys.stderr)\n"
        "print(json.dumps({'message': ticket.strip()}).replace('m', '\\\\u006d'), file=sys.stderr)\n"
        "sys.exit(1)\n"
    )
    assert cli.main(["mount", str(tmp_path)]) == 1
    out, err = capfd.readouterr()
    assert _TICKET not in out + err
    assert "[redacted]" in out
    assert err.count("[redacted]") == 2


def test_drains_stderr_while_reading_events(
    cloud: _Cloud,
    mountable: Callable[[str], Path],
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
) -> None:
    mountable(
        "print('diagnostic\\n' * 20000, file=sys.stderr, flush=True)\n"
        "emit(event='mounted', path=directory)\n"
    )
    assert cli.main(["mount", str(tmp_path)]) == 0
    out, err = capfd.readouterr()
    assert _SESSION in out
    assert err.count("diagnostic") == 20000


def test_reports_session_before_foreground_mount_ends_and_unmount_stops_it(
    cloud: _Cloud,
    mountable: Callable[[str], Path],
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
) -> None:
    release = tmp_path / "unmounted"
    mountable(
        "import time\n"
        f"release = Path({str(release)!r})\n"
        "if sys.argv[1] == 'unmount':\n"
        "    release.touch()\n"
        "else:\n"
        "    emit(event='mounted', path=directory)\n"
        "    while not release.exists():\n"
        "        time.sleep(0.01)\n"
        "    emit(event='unmounted', reason='unmount', detail='')\n"
    )
    with ThreadPoolExecutor(max_workers=1) as executor:
        running = executor.submit(cli.main, ["mount", str(tmp_path)])
        try:
            out = ""
            deadline = time.monotonic() + 5
            while _SESSION not in out and time.monotonic() < deadline:
                out += capfd.readouterr().out
                time.sleep(0.01)
            assert _SESSION in out
            assert not running.done()
            assert cli.main(["unmount", str(tmp_path)]) == 0
            assert running.result(timeout=5) == 0
            assert len(cloud.sessions()) == 1
            assert '"event": "unmounted"' in capfd.readouterr().out
        finally:
            release.touch()


@pytest.mark.parametrize("code", [0, 2, 19])
def test_unmount_gets_no_ticket_or_credentials(
    cloud: _Cloud,
    mountable: Callable[[str], Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
    code: int,
) -> None:
    evidence = mountable(
        "assert sys.argv[1] == 'unmount'\n"
        "print('unmount result')\n"
        "print('unmount diagnostic', file=sys.stderr)\n"
        f"sys.exit({code})\n"
    )
    monkeypatch.delenv("REMEMBER_API_KEY")
    assert cli.main(["unmount", str(tmp_path)]) == code
    assert cloud.requests == []
    call = json.loads(evidence.read_text())
    assert call["argv"] == ["unmount", str(tmp_path)]
    assert call["stdin"] == ""
    out, err = capfd.readouterr()
    assert "unmount result" in out
    assert "unmount diagnostic" in err


def test_mount_help_explains_foreground_session_and_install(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit, match="0"):
        cli.main(["mount", "--help"])
    out = capsys.readouterr().out
    assert "foreground" in out
    assert "session_id" in out
    assert "memory:read" in out
    assert "uv tool install mountable" in " ".join(out.split())
